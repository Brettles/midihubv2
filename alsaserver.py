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
        if handleJournal(peer, midi_packet):
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

def handleJournal(peer, packet):
    global logger, outputSocket

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

    if sequenceNumber > peerStatus[peer.name].sequenceNumber+1: # We have missed a packet somewhere
        if not journal.header.a:
            logger.warning('Missed packets but no journal present - continuing')
        elif sequenceNumber == peerStatus[peer.name].sequenceNumber-1 and journal.header.s: # Single packet loss
            logger.warning('Single packet loss identified - continuing')
        else:
            logger.warning(f'This seq={sequenceNumber} > last={peerStatus[peer.name].sequenceNumber} - processing journal')

    logger.info('--- Journal handling ---')
  
    if not journal.header.a and not journal.header.y: logger.info('  Empty journal')
    if journal.header.s: logger.info('   Single packet loss flag')
    if journal.header.h: logger.info('   Enhanced chapter C encoding')

    if journal.header.y: 
        logger.info('--- System Journal ---')
        logger.info(journal.system_journal)
        logger.info('----------------------')

    if journal.header.a:
        for channelNumber in range(journal.header.totchan+1):
            #
            # Issue here is that the pymidi software only thinks there is a
            # single channel in the chapter journal but in reality there can be
            # more than one (hence the loop).
            if channelNumber == 0:
                chapter = journal.channel_journal # Called a Chapter Journal in the RFC
            else:
                logger.warning(f'*** There are {journal.header.totchan} channels here but we only deal with one')
                break # Not right but we'll deal with it later

            logger.info(f'--- Chapter Journal for channel {chapter.header.chan:2g} ---')

            #
            # Process the journal according to RFC6295
            # This first cut does not loop through the journal as it should so
            # if there are multiple channels we may miss something - the logging
            # will reflect that.
            #
            index = 0
    
            if chapter.header.p: # Fixed size of three octets - Appendix A.2
                index += 3
                logger.info('    Program change - ignored')

            if chapter.header.c: # Appendix A.3
                headerFirst = chapter.journal[index]
                length = headerFirst & 0x7f
                index += length
                logger.info(f'    Control change of {length} octets - ignored')

            if chapter.header.m: # Appendix A.4
                headerFirst = chapter.journal[index]&0x03
                headerSecond = chapter.journal[index+1]
                length = headerFirst*256+headerSecond
                index += length
                logger.info(f'    Parameter change of {length} octets - ignored')

            if chapter.header.w: # Fixed size of two octets - Appendix A.5
                wheelFirst = chapter.journal[index]&0x7f
                wheelSecond = chapter.journal[index+1]&0x7f
                pitchWheelValue = wheelFirst*256+wheelSecond
                index += 2
                logger.info(f'    Pitch wheel change to {pitchWheelValue}')

            if chapter.header.n: # Appendix A.6
                headerFirst = chapter.journal[index]
                length = headerFirst & 0x7f
                logger.info(f'    Note on/off of {length*2} octets')

                if headerFirst & 0x80: logger.info('      B (s-bit) set - previous packet had a NoteOff in it')

                if length*2 > len(chapter.journal)-2:
                    logger.warning(f'      WARNING: note on/off header says length is {length*2} but actual length is {len(chapter.journal)-2}')
                    break # Probably not the right thing to do but it is safe

                headerSecond = chapter.journal[index+1]
                high = headerSecond & 0x0f
                low = (headerSecond & 0xf0) >> 4

                index += 2
                for i in range(length): # Length is data length in two-octet groups
                    logger.info(f'      Notes: {hex(chapter.journal[index])} {hex(chapter.journal[index+1])}')
                    index += 2

                #
                # More to do here based on the high and low values above - not implemented yet
                # Things after this probably won't work correctly but that's ok because we only
                # loop once the moment anyway
                #

            if chapter.header.e: # Appendix A.7
                headerFirst = chapter.journal[index]
                length = headerFirst & 0x7f
                index += length
                logger.info(f'    Note extras of {length} octets - ignored')

            if chapter.header.t: # Fixed size of one octet - Appendix A.8
                index += 1
                logger.info('    Channel aftertouch - ignored')

            if chapter.header.a: # Appendix A.9
                headerFirst = chapter.journal[index]
                length = headerFirst & 0x7f
                index += length
                logger.info(f'    Poly aftertouch of {length} octets - ignored')

            logger.info(f'--- End of channel {chapter.header.chan:2g} ---')

    peerStatus[peer.name].sequenceNumber = sequenceNumber

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
