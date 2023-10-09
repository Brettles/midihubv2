#!/usr/bin/python3

from pymidi import server
import pymidi
import logging
import sys
import signal
import alsa_midi

#
# Short-term fix for dealing with some software stability issues.
# This code is designed to receive (only!) RTP MIDI packets and send them to an
# ALSA port. The intention is that there will be other (more stable) code that
# does the RTP send.
#

logger = None

class MyHandler(server.Handler):
    def __init__(self, alsa):
        self.logger = logging.getLogger()
        self.alsaClient = alsa

    def on_peer_connected(self, peer):
        self.logger.info(f'Peer connected: {peer}')

    def on_peer_disconnected(self, peer):
        self.logger.info(f'Peer disconnected: {peer}')

    def on_midi_commands(self, peer, midi_packet):
        for command in midi_packet.command.midi_list:
            self.logger.info(f'{peer.name} sent {command.command}')

            event = None
            if command.command == 'note_on':
                event = alsa_midi.NoteOnEvent(note=command.params.key, velocity=command.params.velocity, channel=command.channel)
            elif command.command == 'note_off':
                event = alsa_midi.NoteOffEvent(note=command.params.key, velocity=command.params.velocity, channel=command.channel)
            elif command.command == 'aftertouch':
                event = alsa_midi.KeyPressureEvent(note=command.params.key, velocity=command.params.touch, channel=command.channel)
            elif command.command == 'pitch_bend_change':
                event = alsa_midi.PitchBendEvent(value=command.params.msb*256+command.params.lsb, channel=command.channel)
            elif command.command == 'control_mode_change':
                event = alsa_midi.ControlChangeEvent(param=command.params.controller, value=command.params.value, channel=command.channel)
            else:
                self.logger.warning(f'Unknown command: {command.command}')
                self.logger.warning(command)

            if event:
                self.logger.info(event)
                self.alsaClient.event_output(event)

        self.alsaClient.drain_output()

def rawServer(midiPort, midiName):
    alsaClient = alsa_midi.SequencerClient(midiName)
    alsaPort = alsaClient.create_port(midiName)

    myServer = server.Server([('0.0.0.0', midiPort)])
    myServer.add_handler(MyHandler(alsaClient))

    myServer._init_protocols()

    while True:
        myServer._loop_once()

def interrupted(signal, frame):
    global logger

    logger.info('Interrupt - stopping')
    sys.exit(0)

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(f'usage: {sys.argv[0]} midi-port alsa-client-port-name')
        sys.exit(1)

    logging.basicConfig()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    signal.signal(signal.SIGINT, interrupted)

    rawServer(int(sys.argv[1]), sys.argv[2])
