#!/usr/bin/python3

#
# Monitor MIDI events on a specific ALSA channel.
#
# This is pretty basic but my intention was to be able to watch for "stuck
# notes". These occur when there is a NoteOn event but no NoteOff. In theory
# this should never happen within the system, but could happen when using RTP
# MIDI. The monitor will only see stuck notes when packets are lost when sent
# to the hub and the journal (for whatever reason) isn't able to correct for
# that.
#

import sys
import logging
import signal
import curses
import alsa_midi

logger = None
stdscr = None
columnCount = 12
columnWidth = 11
logHeight = 5

midiNotes = [
    'C-1', 'C#-1/Db-1', 'D-1', 'D#-1/Eb-1', 'E-1', 'F-1', 'F#-1/Gb-1', 'G-1', 'G#-1/Ab-1', 'A-1', 'A#-1/Bb-1', 'B-1',
    'C0', 'C#0/Db0', 'D0', 'D#0/Eb0', 'E0', 'F0', 'F#0/Gb0', 'G0', 'G#0/Ab0', 'A0', 'A#0/Bb0', 'B0',
    'C1', 'C#1/Db1', 'D1', 'D#1/Eb1', 'E1', 'F1', 'F#1/Gb1', 'G1', 'G#1/Ab1', 'A1', 'A#1/Bb1', 'B1',
    'C2', 'C#2/Db2', 'D2', 'D#2/Eb2', 'E2', 'F2', 'F#2/Gb2', 'G2', 'G#2/Ab2', 'A2', 'A#2/Bb2', 'B2',
    'C3', 'C#3/Db3', 'D3', 'D#3/Eb3', 'E3', 'F3', 'F#3/Gb3', 'G3', 'G#3/Ab3', 'A3', 'A#3/Bb3', 'B3',
    'C4 (middle C)', 'C#4/Db4', 'D4', 'D#4/Eb4', 'E4', 'F4', 'F#4/Gb4', 'G4', 'G#4/Ab4', 'A4 (concert pitch)', 'A#4/Bb4', 'B4',
    'C5', 'C#5/Db5', 'D5', 'D#5/Eb5', 'E5', 'F5', 'F#5/Gb5', 'G5', 'G#5/Ab5', 'A5', 'A#5/Bb5', 'B5',
    'C6', 'C#6/Db6', 'D6', 'D#6/Eb6', 'E6', 'F6', 'F#6/Gb6', 'G6', 'G#6/Ab6', 'A6', 'A#6/Bb6', 'B6',
    'C7', 'C#7/Db7', 'D7', 'D#7/Eb7', 'E7', 'F7', 'F#7/Gb7', 'G7', 'G#7/Ab7', 'A7', 'A#7/Bb7', 'B7',
    'C8', 'C#8/Db8', 'D8', 'D#8/Eb8', 'E8', 'F8', 'F#8/Gb8', 'G8', 'G#8/Ab8', 'A8', 'A#8/Bb8', 'B8',
    'C9', 'C#9/Db9', 'D9', 'D#9/Eb9', 'E9', 'F9', 'F#9/Gb9', 'G9']

def getCoordinates(note):
    global columnCount, columnWidth

    col = (note%columnCount)*columnWidth
    row = ((int(note/columnCount))*2)+2

    return row, col

def monitorPort(alsaPort):
    global logger, stdscr, columnCount, columnWidth

    midiClient = alsa_midi.SequencerClient('monitor')
    midiPort = midiClient.create_port('input')

    otherClients = midiClient.list_ports()
    sourcePort = None
    for item in otherClients:
        if item.name == alsaPort:
            logging.info(f'Connecting to {item.name}')
            sourcePort = item
            break

    if not sourcePort:
        logger.error(f'Cannot find {alsaPort} to connect to')
        return

    midiPort.connect_from(sourcePort)

    channelsInUse = []
    for index in range(0, len(midiNotes)):
        channelsInUse.append([])

    stdscr = curses.initscr()
    curses.noecho()
    curses.cbreak()
    stdscr.keypad(True)
    curses.curs_set(False)

    columnCount = int(curses.COLS/columnWidth)

    logWin = curses.newwin(logHeight, curses.COLS-1, curses.LINES-6, 0)
    logWin.scrollok(True)

    stdscr.addstr(0, 0, f'Monitoring {sourcePort.name}')

    for index in range(0, len(midiNotes)):
        row, col = getCoordinates(index)
        stdscr.addstr(row, col, midiNotes[index])

    stdscr.move(0, 0)
    stdscr.refresh()

    logOutput = [''] * 5

    while True:
        updateScreen = False
        midiEvent = midiClient.event_input()

        if midiEvent.type == alsa_midi.EventType.NOTEON:
            if midiEvent.channel not in channelsInUse[midiEvent.note]:
                channelsInUse[midiEvent.note].append(midiEvent.channel)
                channelsInUse[midiEvent.note].sort()
                updateScreen = True
        elif midiEvent.type == alsa_midi.EventType.NOTEOFF:
            if midiEvent.channel in channelsInUse[midiEvent.note]:
                channelsInUse[midiEvent.note].remove(midiEvent.channel)
                updateScreen = True
        else:
            logOutput.insert(0, str(midiEvent))
            if len(logOutput) > logHeight: logOutput.pop()

            logWin.clear()
            for index in range(0, logHeight):
                logWin.move(index, 0)
                logWin.addstr(logOutput[index])
            logWin.refresh()

        if updateScreen:
            row, col = getCoordinates(midiEvent.note)
            stdscr.addstr(row+1, col, columnWidth*' ');
            stdscr.addstr(row+1, col, ','.join([str(i) for i in channelsInUse[midiEvent.note]])[:columnWidth-1], curses.A_STANDOUT)
            stdscr.move(0, 0)
            stdscr.refresh()

def interrupted(signal, frame):
    global logger, stdscr

    if stdscr:
        curses.nocbreak()
        stdscr.keypad(False)
        curses.echo()
        curses.endwin()

    logger.info('Interrupt - stopping')
    sys.exit(0)

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(f'Usage: {sys.argv[0]} alsa-client-port-name')
        sys.exit(1)

    logging.basicConfig()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    signal.signal(signal.SIGINT, interrupted)

    monitorPort(sys.argv[1])
