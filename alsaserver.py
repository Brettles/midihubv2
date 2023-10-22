#!/usr/bin/python3

from pymidi import server
from pymidi import packets
import pymidi
import logging
import sys
import signal
import time
import alsa_midi

#
# Short-term fix for dealing with some software stability issues.
# This code is designed to receive (only!) RTP MIDI packets and send them to an
# ALSA port. The intention is that there will be other (more stable) code that
# does the RTP send.
#

logger = None
outputSocket = {}
peerStatus = {}

class peerInfo():
    def __init__(self, peerId):
        self.logger = logging.getLogger()
        self.peerId = peerId
        self.sequenceNumber = None

        #
        # With guidance from RFC4696
        #
        self._status = {}
        self._status['pitchWheel'] = 0x2000

        self._status['noteOnTime'] = [None]*128
        self._status['noteOnSequenceNumber'] = [None]*128
        self._status['noteOnVelocity'] = [None]*128

        self._status['controllerValue'] = [None]*128
        self._status['controllerCount'] = [None]*128
        self._status['controllerToggle'] = [None]*128

        self._status['commandProgramNumber'] = None
        self._status['commabdBankMsb'] = None
        self._status['commabdBankLsb'] = None

        self.channelInfo = [self._status]*16

    def __str__(self):
        return f'peerId {self.peerId} sequenceNumber {self.sequenceNumber}'

    def pitchWheel(self, channel, value=0x2000):
        self.channelInfo[channel]['pitchWheel'] = value

    def noteOn(self, channel, note, velocity=0):
        noteNumber = int(note)
        self.channelInfo[channel]['noteOnTime'][noteNumber] = time.time()
        self.channelInfo[channel]['noteOnSequenceNumber'][noteNumber] = self.sequenceNumber
        self.channelInfo[channel]['noteOnVelocity'][noteNumber] = velocity

    def noteOff(self, channel, note):
        self.noteOn(channel, note, 0) # A velocity of zero indicates NoteOff

    def controlMode(self, channel, controller, value):
        self.channelInfo[channel]['controllerValue'][controller] = value

class MyHandler(server.Handler):
    def __init__(self, alsa):
        self.logger = logging.getLogger()
        self.alsaClient = alsa

    def on_peer_connected(self, peer):
        self.logger.info(f'Peer connected: {peer}')
        peerStatus[peer.name] = peerInfo(peer.name)

    def on_peer_disconnected(self, peer):
        self.logger.info(f'Peer disconnected: {peer}')
        peerStatus.pop(peer.name, None)

    def on_midi_commands(self, peer, midi_packet):
        if handleJournal(peer, midi_packet, self.alsaClient):
            for command in midi_packet.command.midi_list:
                self.logger.info(f'{peer.name} sent {command.command}')

                event = None
                if command.command == 'note_on':
                    event = alsa_midi.NoteOnEvent(note=command.params.key, velocity=command.params.velocity, channel=command.channel)
                    peerStatus[peer.name].noteOn(command.channel, command.params.key, command.params.velocity)
                elif command.command == 'note_off':
                    event = alsa_midi.NoteOffEvent(note=command.params.key, velocity=command.params.velocity, channel=command.channel)
                    peerStatus[peer.name].noteOff(command.channel, command.params.key)
                elif command.command == 'aftertouch':
                    event = alsa_midi.KeyPressureEvent(note=command.params.key, velocity=command.params.touch, channel=command.channel)
                elif command.command == 'pitch_bend_change':
                    pitchWheelValue = command.params.msb*256+command.params.lsb
                    event = alsa_midi.PitchBendEvent(value=pitchWheelValue, channel=command.channel)
                    peerStatus[peer.name].pitchWheel(command.channel, pitchWheelValue)
                elif command.command == 'control_mode_change':
                    event = alsa_midi.ControlChangeEvent(param=command.params.controller, value=command.params.value, channel=command.channel)
                    peerStatus[peer.name].controlMode(command.channel, command.params.controller, command.params.value)
                else:
                    self.logger.warning(f'Unknown command: {command.command}')
                    self.logger.warning(command)

                if event:
                    self.logger.info(event)
                    self.alsaClient.event_output(event)

            self.alsaClient.drain_output()

#
# This is minimal journal handling - not everything as defined in RFC6295.
# We want to handle the worst-case things that happen: NoteOff events that get
# lost and pitch wheel events. Other things are probably going to affect a
# performance and we may have to deal with them later but as this is being
# written those two things are the major contributors to "stuck notes" and
# tuning/pitch problems.
#
# I make not very many apologies for the messiness of this code - the RTP MIDI
# journal system is highly complex - perhaps even more complex than it really
# needs to be (probably in the interest of savings some bytes on the wire).
#
# I've looked for example code for this and there isn't any (that I can find)
# which makes sense given how complex it is - there's a lot of time and effort
# that goes into making this work reliably.
#
# I highly advise that you don't use this in a production environment. It is
# not rigorously tested.
#
def handleJournal(peer, packet, alsaClient):
    global logger, outputSocket, peerStatus

    journal = packet.journal
    sequenceNumber = packet.header.rtp_header.sequence_number

    if not peerStatus[peer.name].sequenceNumber: # This is the first packet from this peer
        peerStatus[peer.name].sequenceNumber = sequenceNumber
        return True

    if not journal:
        peerStatus[peer.name].sequenceNumber = sequenceNumber
        return True

    if sequenceNumber < peerStatus[peer.name].sequenceNumber: # Out of order packet
        logger.warning(f'This seq={sequenceNumber} < last={peerStatus[peer.name].sequenceNumber} - skipping')
        return False

    #
    # Decide what to do based on RFC4696 Section 7
    #
    singlePacketLoss = False
    if sequenceNumber > peerStatus[peer.name].sequenceNumber+1: # We have missed a packet somewhere
        if not journal.header.a:
            logger.warning('Missed packets but no journal present - continuing')
        elif sequenceNumber == peerStatus[peer.name].sequenceNumber-1 and journal.header.s: # Single packet loss
            logger.warning('Single packet loss identified - continuing')
            singlePacketLoss = True
        else:
            logger.warning(f'This seq={sequenceNumber} > last={peerStatus[peer.name].sequenceNumber} - processing journal')

    peerStatus[peer.name].sequenceNumber = sequenceNumber
    if singlePacketLoss: return True

    logger.info('--- Journal handling ---')
  
    if not journal.header.a and not journal.header.y: logger.info('  Empty journal')
    if journal.header.s: logger.info('   Single packet loss flag - ignoring because handling multi packet loss')
    if journal.header.h: logger.info('   Enhanced chapter C encoding')

    if journal.header.y: 
        logger.info('--- System Journal ---')
        logger.info(journal.system_journal)
        logger.info('----------------------')

    if journal.header.a:
        allJournals = journal.channel_journal.journal
        index = 0

        for channelNumber in range(journal.header.totchan+1):
            alsaEvents = []

            #
            # Process the journal according to RFC6295
            #
            # Issue here is that the pymidi software only thinks there is a
            # single channel in the chapter journal but in reality there can
            # be more than one (hence the loop).
            #
            # So we will (re)parse the chapter journal from where we are in
            # the whole journal based on the index - where we are counting
            # our way through the journal, byte-by-byte. The hope here is
            # that the sender hasn't messed up any of the length headers -
            # which does happen so we abort if things don't "smell" right.
            #
            # Header decode as according to chapter 5 in the RFC. I'm reusing
            # the pymidi structure because in future I might find an easier
            # way of doing this (my Python-fu is failing at this point).
            #
            logger.info(f' Loop {channelNumber+1} - index: {index} total length: {len(allJournals)}')
            if channelNumber: # This is not the first loop:
                try:
                    firstByte = allJournals[index]
                    secondByte = allJournals[index+1]
                    thirdByte = allJournals[index+2]
            
                    lengthMsb = firstByte & 0x07
                    lengthLsb = secondByte
            
                    chapter = packets.MIDIChapterJournal(header={'s':firstByte & 0x80,
                                                                 'chan':firstByte & 0x70,
                                                                 'h':firstByte & 0x08,
                                                                 'length': lengthMsb*256+lengthLsb,
                                                                 'p':thirdByte & 0x80,
                                                                 'c':thirdByte & 0x40,
                                                                 'm':thirdByte & 0x20,
                                                                 'w':thirdByte & 0x10,
                                                                 'n':thirdByte & 0x08,
                                                                 'e':thirdByte & 0x04,
                                                                 't':thirdByte & 0x02,
                                                                 'a':thirdByte & 0x01},
                                                         journal = allJournals[index+3])
                except Exception as e:
                    logger.info(f'   Failed to decode chapter header: {e}')
                    break

                index += 3
            else:
                chapter = allJournals

            midiChannel = chapter.header.chan
            logger.info(f'--- Chapter Journal for channel {midiChannel:2g} ---')
    
            if chapter.header.p: # Fixed size of three octets - Appendix A.2
                index += 3
                logger.info('    Program change - ignored')

            if chapter.header.c: # Appendix A.3
                try:
                    header = chapter.journal[index]
                except Exception as e:
                    logger.error(f'    Failed to get control change header: {e}')
                    break

                length = header & 0x7f
                index += length
                logger.info(f'    Control change of {length} octets - ignored')

            if chapter.header.m: # Appendix A.4
                try:
                    headerFirst = chapter.journal[index] & 0x03
                    headerSecond = chapter.journal[index+1]
                except Exception as e:
                    logger.error(f'    Failed to get parameter change header: {e}')
                    break

                length = headerFirst*256+headerSecond
                index += length
                logger.info(f'    Parameter change of {length} octets - ignored')

            if chapter.header.w: # Fixed size of two octets - Appendix A.5
                try:
                    sBit = True if chapter.journal[index] & 0x80 else False # More logic required if this is set (or not)
                    wheelFine = chapter.journal[index] & 0x7f
                    wheelCoarse = chapter.journal[index+1] & 0x7f
                except Exception as e:
                    logger.error(f'    Failed to get pitchwheel header: {e}')
                    break

                pitchWheelValue = wheelCoarse*256+wheelFine
                index += 2
                logger.info(f'    Pitch wheel is {pitchWheelValue}')

                existingValue = peerStatus[peer.name].channelInfo[midiChannel]['pitchWheel']
                if existingValue != pitchWheelValue:
                    logger.warning(f'    Existing value is {existingValue} - changing to {pitchWheelValue}')
#                    alsaEvents.append(alsa_midi.PitchBendEvent(value=pitchWheelValue, channel=midiChannel))
#                    peerStatus[peer.name].pitchWheel(midiChannel, pitchWheelValue)

            if chapter.header.n: # Appendix A.6
                #
                # Logic is from Appendix A.6.1
                #
                try:
                    sBit = True if chapter.journal[index] & 0x80 else False
                    noteOnOctets = chapter.journal[index] & 0x7f
                    high = chapter.journal[index+1] & 0x0f
                    low = (chapter.journal[index+1] & 0xf0) >> 4
                except Exception as e:
                    logger.error(f'    Failed to get NoteOn/Off header: {e}')
                    break

                if noteOnOctets == 127 and low == 15 and high == 0: noteOnOctets = 128

                noteOffOctets = 0
                if low <= high: noteOffOctets = high-low+1
                if low == 15 and (high == 0 or high == 1): noteOffOctets = 0 # Already set but for logic clarity

                logger.info(f'    NoteOn octets: {noteOnOctets*2} NoteOff octets: {noteOffOctets} Low: {low} High: {high} B (S-bit): {sBit}')

                #
                # Commonly seeing numbers where the structure extends past the
                # end of the packet. This is (obviously) not good so we ignore
                # them.
                #
                if noteOnOctets*2 > len(chapter.journal)-2:
                    logger.warning(f'      *** Note on/off header says noteOnOctets is {noteOnOctets*2} but actual length is {len(chapter.journal)-2}')
                    break # Probably not the right thing to do but it is safer this way

                if low != 15 and low > high:
                    logger.error(f'    NoteOff error: low {low} > high {high}')
                    break

                index += 2 # Skip the header bytes
                    
                #
                # First are the NoteOn structures. We ignore these - it's
                # moderately important that we replay these but in the scheme
                # of things if a NoteOn is missed it isn't terrible. So for
                # now, we do nothing with missed NoteOn events.
                #
                for i in range(noteOnOctets): # noteOnOctets is data length in two-octet groups
                    try:
                        noteSBit = True if chapter.journal[index] & 0x80 else False
                        noteYBit = True if chapter.journal[index+1] & 0x80 else False
                        noteOn = chapter.journal[index] & 0x7f
                        velocity = chapter.journal[index+1] & 0x7f
                    except Exception as e:
                        logger.error(f'    Failed to get NoteOn data: {e}')
                        break

                    logger.info(f'       NoteOn {hex(noteOn)} {hex(velocity)} sBit: {noteSBit} yBit: {noteYBit}')
                    index += 2

                #
                # NoteOff events are a different matter. We want to clear them
                # and if we accidentally clear a note that shouldn't have been
                # that's not a terrible outcome. In theory, we can check the
                # state of the existing note but if it's already off and we turn
                # it off again - no big deal.
                #
                noteOffIndex = 8*low
                for i in range(noteOffOctets):
                    try:
                        noteOffBits = chapter.journal[index]
                    except Exception as e:
                        logger.error(f'    Failed to get NoteOff data: {e}')
                        break

                    for bit in reversed(range(8)):
                        bitMask = pow(2, bit)
                        if noteOffBits & bitMask:
                            logger.info(f'       NoteOff {hex(noteOffIndex)}')
                            alsaEvents.append(alsa_midi.NoteOffEvent(note=noteOffIndex, channel=midiChannel))
                            peerStatus[peer.name].noteOff(midiChannel, noteOffIndex)

                        noteOffIndex += 1

                    index += 2

            if chapter.header.e: # Appendix A.7
                try:
                    header = chapter.journal[index]
                except Exception as e:
                    logger.error(f'    Failed to get note extras header: {e}')
                    break

                length = header & 0x7f
                index += length
                logger.info(f'    Note extras of {length} octets - ignored')

            if chapter.header.t: # Fixed size of one octet - Appendix A.8
                index += 1
                logger.info('    Channel aftertouch - ignored')

            if chapter.header.a: # Appendix A.9
                try:
                    header = chapter.journal[index]
                except Exception as e:
                    logger.error(f'    Failed to get poly aftertouch header: {e}')
                    break

                length = header & 0x7f
                index += length
                logger.info(f'    Poly aftertouch of {length} octets - ignored')

            if alsaEvents:
                counter = 1
                for event in alsaEvents:
                    alsaClient.event_output(event)

                    if not counter%8: alsaClient.drain_output()
                    counter += 1
                alsaClient.drain_output()

            logger.info(f'--- End of channel {midiChannel:2g} ---')

    #
    # Send feedback to the MIDI client (this is an Apple thing but everyone seems to do it)
    #
    try:
        packet = packets.AppleMIDIReceiverFeedbackPacket.create(ssrc=peer.ssrc,
                                                                sequence_number=journal.checkpoint_seqnum)
    except Exception as e:
        logger.error(f'Failed to create ReceiverFeedback packet: {e}')
    else:
        try:
            outputSocket['control'].sendto(packet, peer.addr)
        except Exception as e:
            logger.error(f'Failed to send to {peer.addr}: {e}')

    return True

def rawServer(midiPort, midiName):
    global outputSocket

    alsaClient = alsa_midi.SequencerClient(midiName)
    alsaPort = alsaClient.create_port(midiName)

    myServer = server.Server([('0.0.0.0', midiPort)])
    myServer.add_handler(MyHandler(alsaClient))

    myServer._init_protocols()

    for socket in myServer.socket_map:
        if type(myServer.socket_map[socket]) is pymidi.protocol.DataProtocol:
            outputSocket['data'] = myServer.socket_map[socket]
        if type(myServer.socket_map[socket]) is pymidi.protocol.ControlProtocol:
            outputSocket['control'] = myServer.socket_map[socket]

    while True:
        myServer._loop_once()

def interrupted(signal, frame):
    global logger

    logger.info('Interrupt - stopping')
    sys.exit(0)

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(f'usage: {sys.argv[0]} midi-udp-port alsa-client-port-name')
        sys.exit(1)

    logging.basicConfig()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    signal.signal(signal.SIGINT, interrupted)

    rawServer(int(sys.argv[1]), sys.argv[2])