# DotMatrixPy

A Game Boy (DMG) emulator written in Python with audio support! 🎮

---

## Production Period

December 2025

## Overview

DotMatrixPy is a Game Boy (DMG-01) emulator implemented in pure Python. It targets the Sharp SM83 CPU architecture. It provides cycle-accurate timing emulation. The project runs on the PyPy JIT compiler to achieve real-time execution speeds. The codebase mirrors the internal architecture of the hardware. The CPU, PPU, APU, and Bus are separated into distinct classes.

The core logic emulates the SM83 instruction set at the machine-cycle level. The Pixel Processing Unit (PPU) implementation simulates the internal pixel fetcher and FIFO mechanism. It explicitly handles Mode 0-3 timing and STAT interrupts. This enables the correct rendering of scanline-based visual effects. It also supports precise interrupt handling required by some software.

**Technical Specifications:**
*   **CPU**: Implements the full SM83 instruction set with accurate cycle counts and flag handling.
*   **PPU**: Simulates the pixel pipeline (fetcher, FIFO, render mode transitions) accurately to the dot (T-cycle).
*   **Audio**: Generates sound using SDL2. Supports Pulse 1/2, Wave, and Noise channels with envelope and sweep functions.
*   **Memory**: Supports MBC1, MBC3, and MBC5 memory bank controllers for ROM and RAM banking.

## Features

- **CPU**: Full SM83 (LR35902) instruction set emulation
- **PPU**: Accurate pixel processing with proper timing
- **APU**: 4-channel audio (Square × 2, Wave, Noise) with SDL2 playback
- **Timer**: Cycle-accurate timer implementation
- **Input**: Keyboard input for all Game Boy buttons
- **MBC**: Memory Bank Controller support (MBC1, MBC3, MBC5)
- **Save Data**: Battery-backed RAM support with automatic save/load (.sav files)

## Screenshots

> Screenshots taken during emulator testing. All game assets and trademarks belong to their respective owners.

<table>
  <tr>
    <td align="center" width="33%">
      <a href="https://github.com/user-attachments/assets/014523fc-8655-4f00-9f35-16a401c160fb">
        <img src="https://github.com/user-attachments/assets/014523fc-8655-4f00-9f35-16a401c160fb" width="420" alt="Boot ROM - emulator test screenshot" />
      </a><br />
      <b>Boot ROM</b>
    </td>
    <td align="center" width="33%">
      <a href="https://tangramgames.itch.io/tobu-tobu-girl-deluxe">
        <img src="https://github.com/user-attachments/assets/1ef81d81-bf4a-41e3-a67b-86830d4b8cf7" width="420" alt="Tobu Tobu Girl Deluxe - emulator test screenshot" />
      </a><br />
      <b><a href="https://tangramgames.itch.io/tobu-tobu-girl-deluxe">Tobu Tobu Girl Deluxe</a></b>
    </td>
    <td align="center" width="33%">
      <a href="https://izma.itch.io/deadeus">
        <img src="https://github.com/user-attachments/assets/1827a97e-bdd3-4c49-b75c-ef93059bd4d4" width="420" alt="Deadeus - emulator test screenshot" />
      </a><br />
      <b><a href="https://izma.itch.io/deadeus">Deadeus</a></b>
    </td>
  </tr>

  <tr>
    <td align="center" width="33%">
      <a href="https://snorpung.itch.io/dangan-gb">
        <img src="https://github.com/user-attachments/assets/8d698684-9944-45b6-93e7-4d660b0711d8" width="420" alt="Dangan GB - emulator test screenshot" />
      </a><br />
      <b><a href="https://snorpung.itch.io/dangan-gb">Dangan GB</a></b>
    </td>
    <td align="center" width="33%">
      <a href="https://github.com/mattcurrie/dmg-acid2">
        <img src="https://github.com/user-attachments/assets/d8c53d0c-b953-4367-a258-6f874ed56188" width="420" alt="dmg-acid2 - emulator test screenshot" />
      </a><br />
      <b><a href="https://github.com/mattcurrie/dmg-acid2">dmg-acid2</a></b>
    </td>
    <td align="center" width="33%">
      <a href="https://binji.itch.io/porklikegb">
        <img src="https://github.com/user-attachments/assets/af438ebf-d1d9-4690-bbaf-3e6f75b15d0d" width="420" alt="Porklike - emulator test screenshot" />
      </a><br />
      <b><a href="https://binji.itch.io/porklikegb">Porklike</a></b>
    </td>
  </tr>
</table>


## Requirements

- Python 3.10+ (**PyPy recommended**)
- PySDL2
- pysdl2-dll (Windows/macOS)

## Installation

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. (Recommended) Use PyPy for better performance

DotMatrixPy is written in pure Python, so using **PyPy** can provide significant performance improvements (5-20x faster than Python).

#### Installing PyPy

**Windows:**
```bash
# Using winget
winget install PyPy.PyPy3

# Or download from https://www.pypy.org/download.html
```

**macOS:**
```bash
brew install pypy3
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt install pypy3 pypy3-dev
```

#### Running with PyPy

```bash
# Install packages for PyPy
pypy3 -m pip install -r requirements.txt

# Run emulator with PyPy
pypy3 run_rom.py your_game.gb
```

## Running

### Basic usage

```bash
python run_rom.py path/to/rom.gb
```

### Options

```bash
python run_rom.py --help

Options:
  --scale SCALE       Window scale (default: 3)
  --fps FPS           FPS cap (default: 60)
  --headless          Run without a window
  --boot-rom PATH     Path to boot ROM file (optional)
```

### Examples

```bash
# Normal execution
python run_rom.py games/tetris.gb

# Change window size
python run_rom.py --scale 4 games/pokemon.gb

# Run with PyPy for better performance (recommended)
pypy3 run_rom.py games/zelda.gb
```

## Controller

Default keybindings:

| Button | Keyboard    |
|--------|-------------|
| A      | Z           |
| B      | X           |
| Start  | Enter       |
| Select | Right Shift |
| Up     | ↑           |
| Down   | ↓           |
| Left   | ←           |
| Right  | →           |
| Quit   | Escape      |

## Tested Games

The following games have been tested and are playable:

- Tetris
- Super Mario Land
- The Legend of Zelda: Link's Awakening
- Pokémon Gold/Silver
- Kirby's Dream Land
- Donkey Kong
- etc...

Most games should work correctly. However, ROMs that require strict T-cycle accurate timing may have compatibility issues.

## Project Structure

```
DotMatrixPy/
├── gb/
│   ├── apu.py       # Audio Processing Unit
│   ├── bus.py       # Memory bus
│   ├── cartridge.py # ROM/RAM handling, MBC
│   ├── cpu.py       # SM83 CPU
│   ├── gameboy.py   # Main emulator class
│   ├── gpu.py       # VRAM management
│   ├── io.py        # I/O registers (Timer, Joypad, Serial)
│   └── ppu.py       # Pixel Processing Unit
├── tests/
│   └── run_mooneye.py  # Mooneye Test Suite runner
├── run_rom.py       # Main entry point
├── requirements.txt
└── README.md
```

## Accuracy

This emulator passes the Mooneye acceptance test suite for DMG timing accuracy.

## Performance Tips

1. **Use PyPy**: 5-10x faster than Python
2. **Lower scale**: Smaller window = less rendering overhead

## License

MIT License

## Acknowledgments

- [Pan Docs](https://gbdev.io/pandocs/) - Game Boy technical reference
- [Mooneye Test Suite](https://github.com/Gekkio/mooneye-test-suite) - Accuracy testing
- [RGBDS](https://rgbds.gbdev.io/) - Game Boy development toolchain
