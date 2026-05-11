# CHIP-8 ROMs

These ROMs and accompanying notes were copied from the `games` directory of
Kristof Podraczky's `kripod/chip8-roms` repository:

https://github.com/kripod/chip8-roms

The upstream repository republishes the Revival Studios Chip-8 Program Pack.
Its README describes the pack as a collection of CHIP-8, SuperChip, and
MegaChip8 programs found around the web, with author and release-year metadata
added for many ROMs, and states that the package can be freely distributed in
its original form.

Additional ROMs under `archive/` were copied from John Earnest's Chip8
Community Archive:

https://github.com/JohnEarnest/chip8Archive

The archive's `programs.json` and `authors.json` files are included beside the
ROMs and are used by the Altoids picker to show titles, authors, descriptions,
events, release dates, platforms, and selected Octo runtime options. The
archive README states that repository contents are placed under Creative
Commons 0.

Keep additional downloaded ROMs in this directory with `.ch8`, `.rom`, `.bin`,
or no extension so the Altoids emulator screen can discover them recursively. A
`.txt` file with the same base filename will be shown as cartridge details in
the picker, and `programs.json` metadata is used when available.
