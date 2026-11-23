# THERM skills: what they are and how to use them

This note is for facade and civil engineers who use LBNL THERM. It explains what the skills do in plain words. The exact commands are in `README.md`.

## What they are

The skills are seven small Python tools that Claude Code can use for you. You ask in plain English, for example "what is the frame U-factor of this sill?". Claude Code then picks the right tool, runs THERM in the background and reads the answer back.

Everything runs on your own Windows PC. THERM solves with no window and no clicks, and no file leaves your computer.

The default THERM version is 7.8.80, the version NFRC certification uses. THERM 8.1 also works, but it is a beta.

## The seven skills

| Skill | In one line |
|---|---|
| therm-setup | Checks that your PC can run THERM without its window, and solves THERM's sample sill as a test. |
| therm-library | Finds materials and boundary conditions, and makes a small project library (NFRC and ISO 10077-2 sets included). |
| therm-glazing | Turns the glazing into plain solids: glass panes plus one solid per gap, or a calibration panel. |
| therm-model | Builds a THERM model from a list of polygons and boundaries, shows what is inside a model, or changes one value in it. |
| therm-run | Solves one model or a whole folder, one at a time, and says which ones solved. |
| therm-results | Reads U-factors and surface temperatures from a solved model into a table. |
| therm-frame-calc | Works out ISO 10077-2 Uf and psi, or the NFRC frame and edge table for WINDOW. |

## How they chain

A typical job runs left to right. You can stop at any step.

```
setup (once) -> library -> glazing -> model -> run -> results -> frame-calc
```

- **setup** runs once on a new PC.
- **library** collects the materials and boundary conditions the section needs.
- **glazing** fills the glazing zone with glass and gap solids, or with a calibration panel for an ISO Uf run.
- **model** writes the THERM file.
- **run** solves it.
- **results** reads the U-factors and temperatures.
- **frame-calc** turns them into Uf, psi or the NFRC table.

You can also start from a model you drew in THERM yourself. Save it as a `.thmx` file and start at run, or at model if you want to change a value first.

## Setting up once

1. Install THERM 7.8.80 from LBNL's download page.
2. Install the two small runtimes THERM needs, if they are missing: the Microsoft VC++ x86 runtime and LBNL's Intel Fortran package. The setup check tells you which ones are missing.
3. Install Python 3.10 or newer, and tick "Add python.exe to PATH".
4. Save LBNL's command-line password once as a Windows setting named `THERM_CLI_PW`. LBNL posted the password on its THERM forum, and the README shows a one-line way to save it.
5. Copy the seven skill folders into Claude Code's skills folder.
6. Ask Claude Code to "check my THERM setup". It solves THERM's sample sill and compares the result with THERM's own values: Frame 2.016345, Edge 2.337976 and SHGC Exterior 1.876081 W/m2K.

## Everyday requests

You can ask for things like these:

- "Check my THERM setup."
- "Solve every section in C:\jobs\facade\sections and tell me which ones failed."
- "What are the frame and edge U-factors in solved\sill.thmx?"
- "Make the NFRC frame and edge table for these three sills."
- "Change the wood conductivity in sill.thmx to 0.13 and solve it again."
- "Swap the thermal break to PA66 GF25, k 0.3, and compare the frame U-factor before and after."
- "Build this aluminium sill from my sketch coordinates, with ISO boundary conditions."
- "Run the ISO 10077-2 Uf and psi for this section: one run with a calibration panel, one with the glazing."
- "Export the results of this folder to CSV."
- "Which boundary conditions are in this model?"

Claude Code shows you each step it takes. Every result comes from THERM's own solve, never from an estimate.

## Things to know

- **Glazing is modelled as plain solids.** A THERM 7.8 file written by the tools holds no glazing-system data. Glass is a solid with k = 1.0 W/mK, and each gap is a solid with an effective conductivity that you give (for example from WINDOW or EN 673). THERM then runs no condensation-resistance case.
- **Read Projected Y on a sill.** THERM writes a "projected in glass plane" value for these models, but it is wrong (it falls back to the X projection). The tools never show it. On a sill, Projected Y gives the right frame and edge values. For heads, jambs and mullions the tools ask you which projection to use, because that case was not checked.
- **No error norm.** THERM 7.8 does not write its error energy norm anywhere the tools can read. If you need it, open the solved file in THERM.
- **One THERM at a time.** All THERM 7.8 processes on a PC, including the THERM window you have open, share one working folder. Two solves at once can fail or give slightly different numbers. Close the THERM window before you ask for a solve. The tools refuse to start while THERM is open.
- **No extra dots in file names.** THERM hangs on a name such as `gap18.51.thmx`. The tools refuse such a name and suggest `gap18p51.thmx`. Dots in folder names are fine.
- **Check ISO values yourself.** The ISO 10077-2 formulas, the 0.035 W/mK panel and the surface resistances were not checked against the standard's text. Every ISO calc sheet lists its inputs and formulas so you can check them.
- **NFRC values are for checking.** The NFRC table is for entering or checking values in WINDOW. Certification still needs a full NFRC model made in the THERM window, with its glazing system.
- **Licence.** THERM's licence does not allow service-bureau use. Use the tools for your own work on your own PC.
- **Review in THERM.** You can open any solved file in THERM to look at the mesh, isotherms and U-factors.

## If something goes wrong

- **"Password not set" or no log.** Save `THERM_CLI_PW` again, then close and reopen Claude Code.
- **Code 0xC0000135.** The Intel Fortran runtime is missing. Install LBNL's package.
- **"THERM7.exe is already running".** Close the THERM window and ask again.
- **"Unknown material".** The model names a material that is not in the file or in THERM's library. Add it with therm-library.
- **A file timed out.** THERM was waiting on a dialog box. The error shows the dialog's text.
