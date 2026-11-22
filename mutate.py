#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Nicolas Rooney
"""Break the engine on purpose and confirm check.py notices.

A check that has never been watched to fail has an unknown polarity and an
unknown subject.  Several of the mutations below found checks that had passed
since the day they were written and guarded nothing -- see INTERNALS section 11,
which records each one and why it was passing.

The count is deliberately not written down here.  It was, and it went stale
the first time a milestone added to the table; `python3 mutate.py --verify'
prints it, along with whether every one still finds its target.

    python3 mutate.py --verify        # no compiles: do they all still apply?
    python3 mutate.py                 # all of them
    python3 mutate.py sky-ignores-facing

Each run copies the sources into build/mut/<name>/, applies one edit, and
reports which checks went red.  "NOTHING WENT RED" is a finding, not a pass.

A full pass is one whole `check.py' per mutation and is an overnight job on
a slow machine.  `apply(name, dir)' builds a mutant tree without running
anything in it, which is how to drive one mutation against only the checks
it is aimed at.
"""
import os, re, shutil, subprocess, sys

SRC = os.path.dirname(os.path.abspath(__file__))
# What a mutant tree is made of: the engine, the harness and what the harness
# drives.  Named rather than globbed, so a stray file beside them -- a
# recording, a fixture left by a run -- is never copied into every mutant.
SOURCES = ["doom.tex", "wad.tex", "play.tex", "test.wad", "check.py",
           "play.py", "gif.py", "dump.py", "maketest.py"]
IWADS = ["doom2.wad", "doom.wad", "doom1.wad"]
MUTS = {
 # name : (file, old, new)
 "flat-row-offset": ("wad.tex",
    r"\number\numexpr\dtFP+64*\dtFV\relax\space length 64",
    r"\number\numexpr\dtFP+64*\dtFV+1\relax\space length 64"),
 "flats-never-chosen": ("doom.tex",
    r"      \ifnum\csname dtFw\the\dtPFI\endcsname=0 \dtPFI=-1 \fi",
    r"      \dtPFI=-1"),
 "camera-x-dropped": ("doom.tex",
    r"\edef\dt@pxq{\number\numexpr\PX/4096+268435456\relax}%",
    r"\edef\dt@pxq{\number\numexpr268435456\relax}%"),
 "plane-light-fixed": ("doom.tex",
    r"\csname dtCM\csname dtCC\number\numexpr\dt@psm-\dt@plit/(#3+256)+128\relax",
    r"\csname dtCM\csname dtCC\number\numexpr\dt@psm+128\relax"),
 "sky-drawn-as-flat": ("wad.tex",
    r"      \dtFN=-2 \global\dtHASSKY=1",
    r"      \dtWantFlat"),
 "sky-ignores-facing": ("doom.tex",
    r"      \tmpa=\numexpr\PA+\csname dtKA\the\cx\endcsname\relax",
    r"      \tmpa=\numexpr\csname dtKA\the\cx\endcsname\relax"),
 "sky-wrong-row-scale": ("doom.tex",
    r"        \tmpb=\numexpr100+(\tmpi-\HALFH)*200/\SCRH\relax",
    r"        \tmpb=\numexpr100+(\tmpi-\HALFH)\relax"),
 "texture-pick-stuck": ("doom.tex",
    r"""  \expandafter\let\expandafter\dt@pick
    \csname dt@pg\number\numexpr\dtTU-\dtTG\relax\endcsname""",
    r"""  \expandafter\let\expandafter\dt@pick
    \csname dt@pg0\endcsname"""),
 # ---- M13: the sector machinery ------------------------------------------
 # A tag that matches everything, which is what a trigger looks like when the
 # match is dropped: every marked mover starts at once and the map still
 # animates plausibly.
 "tag-ignored": ("doom.tex",
    r"      \ifnum\dtSecTag{\the\dtTAGN}=#1\relax",
    r"      \ifnum0=0\relax"),
 # Acts on the first sector carrying the tag and stops.  One of MAP01's
 # switches names four sectors, and three of them staying put is invisible
 # in any aggregate count.
 "tag-first-only": ("doom.tex",
    r"        \dtMVSEC=\dtTAGN \dt@tagdo",
    r"        \dtMVSEC=\dtTAGN \dt@tagdo \dtTAGN=\NSEC"),
 "once-only-not-spent": ("doom.tex",
    """    \\ifnum\\dtTREP=0
      \\expandafter\\xdef\\csname dtLU\\the\\dtFLINE\\endcsname{1}%
    \\fi""",
    """    \\ifnum\\dtTREP=9
      \\expandafter\\xdef\\csname dtLU\\the\\dtFLINE\\endcsname{1}%
    \\fi"""),
 "switch-face-stuck": ("doom.tex",
    r"  \ifnum\dtTACT=2 \dt@swline{\the\dtFLINE}\fi",
    r"  \ifnum\dtTACT=9 \dt@swline{\the\dtFLINE}\fi"),
 "crossing-never-fires": ("doom.tex",
    r"  \ifnum\dtTACT=3 \dtFireLine{\the\dtXGL}\fi",
    r"  \ifnum\dtTACT=9 \dtFireLine{\the\dtXGL}\fi"),
 # The camera stops riding a moving floor.  The floor still moves and the
 # frame is still steady, which is exactly how this shipped the first time.
 "plat-never-rides": ("doom.tex",
    r"  \ifnum\dtMVSEC=\dtSECN \dtFollowFloor \fi",
    r"  \ifnum\dtMVSEC=-1 \dtFollowFloor \fi"),
 # A lift whose target is its own floor: marked, ticked, and motionless.
 "lift-target-own-floor": ("doom.tex",
    r"  \ifnum\csname dtSf#1\endcsname<\dtMVX \dtMVX=\csname dtSf#1\endcsname\relax\fi",
    r"  \relax"),
 # Fires every marked mover rather than the ones the tag names.  Marking-time
 # mutations cannot show this up: MAP01's switch 84 lowers to the highest
 # neighbouring floor, and for the lift's sector that target IS its own floor,
 # so a mis-marked lift sits still and looks innocent.  Only a fire-time bug
 # sends the lift's own recorded target (40) to a sector tagged 3.
 # Repointed at M16's \ifcase dispatch: this used to name the \ifnum form of
 # \dt@tagdo, and when M16 rewrote it the mutation stopped applying without
 # anything going red.  `python mutate.py --verify' is what found that, and
 # is why it exists.
 "fires-every-mover": ("doom.tex",
    """    \\def\\dt@tagdo{\\ifcase\\dtTKIND \\or
        \\dtDSEC=\\dtMVSEC \\dt@firedoor \\or
        \\dt@fireplat \\or
        \\dtCESEC=\\dtMVSEC \\dt@fireceil \\or
        \\dt@firestair \\or
        \\dt@firelight \\or \\or
        \\dt@fireteleport \\or
        \\dt@fireraise \\fi}%
    \\dt@fortag{\\dtLineTag{\\the\\dtFLINE}}%""",
    """    \\dtMVI=0
    \\dtWhile{\\dtMVI<\\dtNPLAT}{%
      \\dtMVSEC=\\csname dtEX\\the\\dtMVI\\endcsname\\relax
      \\dt@fireplat\\advance\\dtMVI by1\\relax}%"""),
 # ---- M14: pickups and the status bar ------------------------------------
 # A thing that is collected but never marked.  It heals every frame it is
 # stood next to and never disappears, which on a scripted walk that keeps
 # moving looks like nothing at all.
 "pickup-never-spent": ("doom.tex",
    r"\expandafter\gdef\csname dtHG\the\dtTUI\endcsname{1}%",
    r"\relax"),
 # Take everything touched, whether or not it gave anything.  This is
 # \dtFIRED's lesson from M13 in a new place: the medikit a full-health
 # player cannot use still vanishes off the floor.
 # The long form is deliberate -- the short one appears in a comment above,
 # and mutate.py replaces the FIRST occurrence.
 "pickup-ignores-refusal": ("doom.tex",
    "  \\ifnum\\dtGOT=1\n    \\expandafter\\gdef\\csname dtHG",
    "  \\ifnum0=0\n    \\expandafter\\gdef\\csname dtHG"),
 # Doom compares two objects' heights as well as their footprints.  Without
 # it a stimpack on a ledge is collectable from the floor underneath it, and
 # no aggregate count of anything can see that.
 "pickup-height-ignored": ("doom.tex",
    r"      \ifnum\dtTUD<\PHEAD \dt@give \fi",
    r"      \dt@give"),
 # A weapon that is collected but never comes up in your hands.  The ammo
 # still arrives, so the pickup looks like it worked.
 "weapon-never-switches": ("doom.tex",
    r"    \dtWEAP=#1\relax",
    r"    \relax"),
 # The bar reads a fixed ammunition slot rather than the weapon in hand: a
 # shotgun that reports your bullets.
 "ammo-always-bullets": ("doom.tex",
    r"  \csname dtUA\csname dtUT\the\dtWEAP\endcsname\endcsname\fi}",
    r"  \csname dtUA0\endcsname\fi}"),
 # Digits advanced by each glyph's own width instead of the width of the
 # digit zero.  Only a number containing a 1 is laid out differently, and
 # only by three pixels.
 # Written against \dt@hudnumP's prefix rather than against STTNUM: M17 split
 # the drawer so the small numbers could share it, and a mutation left
 # pointing at the old line reports MUTATION DID NOT APPLY -- which prints as
 # a line of output and not as a failure, i.e. exactly the silent kind.
 "digit-pitch-per-digit": ("doom.tex",
    r"    \dtHDW=\csname dtPw#10\endcsname\relax",
    r"    \dtHDW=\csname dtPw#11\endcsname\relax"),
 # Numbers written left to right from the anchor, i.e. left-aligned.  Every
 # digit is still drawn and still correct.
 "digits-march-right": ("doom.tex",
    r"  \advance\dtHDX by-\dtHDW",
    r"  \advance\dtHDX by\dtHDW"),
 # ST_calcPainOffset with a rounding division.  Wrong for one health value
 # in five, by one face.
 "face-pain-rounds": ("doom.tex",
    r"  \divide\dtHDN by101",
    r"  \dtHDN=\numexpr\dtHDN/101\relax"),
 # Armour that is always given, so a green jacket downgrades a blue one.
 "armour-always-given": ("doom.tex",
    r"  \ifnum\dtARMOR<\numexpr100*(#1)\relax",
    r"  \ifnum0=0 "),
 # A backpack that doubles the maxima every time it is touched.  One backpack
 # per map is the norm, so this is invisible on any stock level.
 "backpack-doubles-twice": ("doom.tex",
    r"  \ifnum\dtBACKP=0",
    r"  \ifnum0=0 "),
 # The tag read two bytes late, i.e. out of the sidedef field next to it.
 "linedef-tag-late": ("wad.tex",
    r"          \dtLTG=\dtS{\number\numexpr\dtLDP+14*\dtJ+8\relax}\relax",
    r"          \dtLTG=\dtS{\number\numexpr\dtLDP+14*\dtJ+10\relax}\relax"),
 # ---- M15: enemies and combat --------------------------------------------
 # Sight that no wall stops.  Every monster on the map wakes on the first
 # frame and the level still plays, which is what makes this worth a
 # mutation: it looks like the engine working, only more so.
 "sight-sees-through-walls": ("doom.tex",
    r"    \ifnum\dtSTH=1 \dt@sightblock \fi",
    r"    \relax"),
 # ...and the opposite: a sight line nothing blocks because nothing is ever
 # tested.  Monsters never wake at all.
 "sight-blocked-always": ("doom.tex",
    r"  \dtSEE=1 \dtSTI=0",
    r"  \dtSEE=0 \dtSTI=\NSEG"),
 # A_Look never fires, so nothing leaves its spawn loop.  The map still
 # animates, because the spawn loop is an animation.
 "monsters-never-wake": ("doom.tex",
    r"      \ifnum\dtSEE=1 \dt@monset{1}\fi",
    r"      \relax"),
 # Monsters chase and posture but their shots never land.  The face never
 # reacts, and nothing else in the suite can see the difference.
 "damage-never-lands": ("doom.tex",
    r"    \advance\dtHEALTH by-\dtDMA",
    r"    \relax"),
 # Armour that absorbs nothing.  Health falls faster and the armour count
 # sits still, which no aggregate reading of either can distinguish.
 "armour-absorbs-nothing": ("doom.tex",
    r"      \dtDMC=\dtDMA \divide\dtDMC by\dtDMB",
    r"      \dtDMC=0 "),
 # The aim scan ignores how far off the ray a monster is, so every shot hits
 # whatever happens to be in front of the player, however wide.
 "aim-ignores-the-miss": ("doom.tex",
    r"        \ifnum\dtSHQ<\csname dtOr\csname dtHt\the\dtSHN\endcsname\endcsname",
    r"        \ifnum0<1 "),
 # A monster that dies but is never counted.  Corpses still pile up.
 "kills-never-counted": ("doom.tex",
    r"    \global\advance\dtKILLS by1",
    r"    \relax"),
 # Death without a corpse: the animation loops for ever instead of stopping
 # on its last frame, so a dead zombieman twitches on the floor.
 "death-never-settles": ("doom.tex",
    r"    \expandafter\xdef\csname dtOs\the\dtMBI\endcsname{5}%",
    r"    \relax"),
 # A corpse that is still solid, which is Doom's MF_SOLID never cleared.
 "corpses-still-block": ("doom.tex",
    r"  \ifnum\csname dtOs\the\dtBKI\endcsname>3 \else",
    r"  \ifnum\csname dtOs\the\dtBKI\endcsname>9 \else"),
 # Firing costs nothing, so the pistol never runs dry.
 "ammo-never-spent": ("doom.tex",
    r"""    \expandafter\xdef\csname dtUA\the\dtSHT\endcsname
      {\number\numexpr\csname dtUA\the\dtSHT\endcsname-\dtSHP\relax}%""",
    r"    \relax"),
 # ---- M16: ceilings, stairs, lights, exit --------------------------------
 # A crusher that stops at the bottom instead of coming back.  It is a
 # perfectly good ceiling-lower, and it crushes on the way down, so
 # everything except the second descent looks right.
 "crusher-never-returns": ("doom.tex",
    """    \\ifcsname dtRC\\the\\dtCESEC\\endcsname
      \\expandafter\\xdef\\csname dtRS\\the\\dtCESEC\\endcsname{2}%
    \\else""",
    """    \\ifnum0=1
      \\expandafter\\xdef\\csname dtRS\\the\\dtCESEC\\endcsname{2}%
    \\else"""),
 # A ceiling that closes on the player and costs them nothing.
 "crusher-does-no-damage": ("doom.tex",
    r"      \dtDamagePlayer{\the\CRUSHDMG}{\the\dtPSX}{\the\dtPSY}%",
    r"      \relax"),
 # The stop line does not stop anything.  The crusher carries on, which is
 # exactly what it was doing before the line was crossed.
 "crusher-stop-ignored": ("doom.tex",
    r"    \ifnum\dtTFUN=4",
    r"    \ifnum\dtTFUN=9"),
 # Every step of a staircase rises to the same height rather than
 # accumulating, i.e. a platform rather than stairs.
 "stairs-do-not-accumulate": ("doom.tex",
    r"      \advance\dtSTPH by\dtSTPN",
    r"      \relax"),
 # The chain ignores the floor flat, so a staircase runs through every
 # sector it can reach.  Invisible on a map whose floors all match.
 "stairs-ignore-the-flat": ("doom.tex",
    r"      \ifnum\dtSecFlat{#1}=\dtSTPF \dtSTPK=#1\relax\fi",
    r"      \dtSTPK=#1\relax"),
 # Every light special sets 255, so "to the brightest neighbour" stops
 # reading the neighbour and the room is merely bright.
 "light-always-full": ("doom.tex",
    r"    \or \dt@lightnear{1}%",
    r"    \or \dtLTV=255\relax"),
 # The exit is reached and recorded and the level carries on regardless.
 "exit-does-not-end-the-level": ("doom.tex",
    r"    \ifnum\dtEXIT>0 \dtFRAME=\NFRAMES \fi",
    r"    \relax"),
 # ---- M17: the rest of the status bar ------------------------------------
 # The arms panel always draws the "not owned" number, so a shotgun you are
 # holding still reads as one you have not found.
 "arms-never-lit": ("doom.tex",
    r"    \dt@hudpat{\ifcsname dtUW\number\numexpr\dtHDI+2\relax\endcsname",
    r"    \dt@hudpat{\ifcsname dtUWnope\number\numexpr\dtHDI+2\relax\endcsname"),
 # The ammunition boxes read the maximum into both columns, so a backpack
 # looks like it filled you up.
 "ammo-box-shows-the-max": ("doom.tex",
    r"    \dt@hudnumP{STYSNUM}{\csname dtUA\the\dtHDI\endcsname}{288}{3}%",
    r"    \dt@hudnumP{STYSNUM}{\csname dtUM\the\dtHDI\endcsname}{288}{3}%"),
 # A key you are holding puts no icon on the bar.
 "keys-never-shown": ("doom.tex",
    r"    \ifcsname dtUK\the\dtHDI\endcsname",
    r"    \ifcsname dtUKnope\the\dtHDI\endcsname"),
 # ---- M18: projectiles ---------------------------------------------------
 # A missile nothing can stop.  It leaves the map and flies on for ever,
 # which every reading of the frames still shows as a missile in flight --
 # the only thing that knows better is where it ends up.
 "missile-never-stopped": ("doom.tex",
    """    \\ifnum\\dtBLK=1
      \\dtJT=1
    \\else""",
    """    \\ifnum\\dtBLK=9
      \\dtJT=1
    \\else"""),
 # A missile tested with the WALKER's rule: it climbs a step it should have
 # hit and stops at one it should have flown over.  On any map whose floors
 # are level this changes nothing at all, which is why the fixture builds
 # the height difference on purpose.
 "missile-ignores-its-height": ("doom.tex",
    """      \\ifdt@mis
        \\ifnum\\dtMISZ<\\dtOB \\dtBLK=1 \\fi
        \\ifnum\\numexpr\\dtMISZ+\\dtMHGT\\relax>\\dtOT \\dtBLK=1 \\fi
      \\else""",
    """      \\ifnum0=1
        \\ifnum\\dtMISZ<\\dtOB \\dtBLK=1 \\fi
        \\ifnum\\numexpr\\dtMISZ+\\dtMHGT\\relax>\\dtOT \\dtBLK=1 \\fi
      \\else"""),
 # The bug this section actually shipped with for an hour.  Splitting the z
 # velocity per substep instead of taking it over the whole frame rounds it
 # away from zero every time, so a fireball descends about a third faster
 # than it was aimed and lands in the floor a stride in front of the player.
 # The missile still flies, still explodes, still animates and still draws;
 # what it stops doing is arriving.
 "missile-z-per-substep": ("doom.tex",
    r"    \dtJD=\numexpr\csname dtHz\the\dtJI\endcsname+\dtJW*\dtJO/\dtJN\relax",
    r"    \dtJD=\numexpr\dtJZ+\dtJW/\dtJN\relax"),
 # A missile that arrives, explodes, and costs its victim nothing.
 "missile-does-no-damage": ("doom.tex",
    """    \\dtMBI=\\dtMHIT
    \\dtHurtThing{\\the\\dtJD}%""",
    "    \\relax"),
 # A blast that does full damage everywhere inside its radius.  Everything
 # near a rocket still dies and the player is still hurt by their own; only
 # the falloff is gone, and no count of either can see that.
 "blast-does-not-fall-off": ("doom.tex",
    r"    \dtDamagePlayer{\number\numexpr\dtJH-\dtJD\relax}{\the\dtJX}{\the\dtJY}%",
    r"    \dtDamagePlayer{\the\dtJH}{\the\dtJX}{\the\dtJY}%"),
 # Spent missiles keep their slots.  \dtMAXMIS fills up and firing quietly
 # stops working part way through a run -- which reads as a weapon that ran
 # out of ammunition, except that the ammunition is still there.
 "missiles-never-recycled": ("doom.tex",
    r"      \ifnum\csname dtJs\the\dtJJ\endcsname=2 \dtJI=\dtJJ \fi",
    r"      \relax"),
 # A monster that decides to throw something and then does not, so the imp
 # goes back to being M15's imp -- with the attack animation still playing.
 "monster-never-throws": ("doom.tex",
    """    \\ifcsname dtJm\\dt@mty\\endcsname
      \\ifnum\\dtMBD<\\dtMRANGE
        \\ifnum\\dtMBD<\\dtMELEE \\else \\dt@monmissile \\fi
      \\fi
    \\fi""",
    "    \\relax"),
 # Missiles that exist, fly, hit and hurt, and are invisible.
 "missile-never-drawn": ("doom.tex",
    r"      \ifnum\csname dtJs\the\dtTI\endcsname<2 \dt@thinggeomB \fi",
    r"      \relax"),
 # ---- M19: masked middle textures ----------------------------------------
 # The drawseg list is never filled, so a masked middle is composited opaque
 # at load and then nothing draws it: the bars simply are not there, and the
 # room behind them looks exactly as it should.
 "masked-middle-never-recorded": ("doom.tex",
    r"  \dt@maskrec",
    r"  \relax"),
 # The pegging bit is ignored, so a railing that should stand on the floor
 # of its opening hangs from the ceiling instead -- the whole height of the
 # texture out of place, and still a perfectly convincing set of bars.
 "masked-middle-ignores-pegging": ("doom.tex",
    """  \\ifnum\\dtPEGF>1
    \\dtKU=\\numexpr\\dtKB+\\dtKH+\\dtVOFF\\relax
  \\else""",
    """  \\ifnum\\dtPEGF>9
    \\dtKU=\\numexpr\\dtKB+\\dtKH+\\dtVOFF\\relax
  \\else"""),
 # Every column of the texture reads column 0, so the bars stop being bars:
 # whatever is in that one column is smeared across the whole opening.
 "masked-middle-one-column": ("doom.tex",
    r"  \dtKC=\numexpr\rd-\dtKC\relax \ifnum\dtKC<0 \advance\dtKC by\dtKW \fi",
    r"  \dtKC=0 "),
 # Drawn with no regard for the silhouette the wall pass recorded, so the
 # bars show through whatever is standing in front of them.
 "masked-middle-ignores-the-clip": ("doom.tex",
    """    \\dtTSC=\\ra
    \\dt@thingclip""",
    """    \\dtTSC=\\ra
    \\dtSPCT=0 \\dtSPCB=\\SCRH"""),
 # ---- M20: locked doors, infighting, sound -------------------------------
 # Every locked door opens for anybody, which is the world M19 was in and
 # looks exactly like a door working.
 "locks-are-not-checked": ("doom.tex",
    r"      \ifcsname dtUK\number\numexpr\dtNEEDKEY-1\relax\endcsname\else",
    r"      \ifnum0=0 \else"),
 # The door still refuses -- so every reading of the refusal itself stays
 # green -- but the refusal SPENDS the line, which is what a key test placed
 # after the tag scan instead of before \dt@fireact would do.  A once-only
 # locked door is then dead before the key is ever found.
 #
 # Repointed once: the first version swapped \dt@keytest and \dt@fireact,
 # which made every locked door open and reddened "refuses a player without
 # its key" instead of the check it was named for.  A mutation that goes red
 # somewhere is not the same as a mutation that goes red where it meant to.
 "locked-refusal-spends-the-line": ("doom.tex",
    """      \\dt@keytest
      \\ifnum\\dtLOCKED=0 \\dt@fireact \\fi""",
    """      \\dt@keytest
      \\ifnum\\dtTREP=0
        \\expandafter\\xdef\\csname dtLU\\the\\dtFLINE\\endcsname{1}%
      \\fi
      \\ifnum\\dtLOCKED=0 \\dt@fireact \\fi"""),
 # A monster hurt by another never notices: \dtHURTBY is recorded and
 # thrown away, so the crossfire lands and nobody turns round.
 "no-retaliation": ("doom.tex",
    """    \\ifnum\\dtHURTBY=\\dtMBI \\else
      \\expandafter\\xdef\\csname dtOt\\the\\dtMBI\\endcsname{\\the\\dtHURTBY}%
    \\fi""",
    r"    \relax"),
 # Targets are read but every attack still lands on the player, so two
 # monsters can chase each other for ever and never trade a shot.
 "attacks-always-hit-the-player": ("doom.tex",
    r"  \ifnum\dtMTI<0" + "\n" + r"    \dtDamagePlayer{#1}{\the\dtMBX}{\the\dtMBY}%",
    r"  \ifnum0=0 " + "\n" + r"    \dtDamagePlayer{#1}{\the\dtMBX}{\the\dtMBY}%"),
 # A missile no longer passes through its own species, so the first fireball
 # thrown in a room of imps starts a brawl Doom would never have started.
 "missiles-hit-their-own-kind": ("doom.tex",
    r"      \ifnum\csname dtHt\the\dtBKI\endcsname=\csname dtHt\the\dtMSELF\endcsname",
    r"      \ifnum\csname dtHt\the\dtBKI\endcsname=-9"),
 # Nothing is ever woken by a gunshot.
 "gunshots-are-silent": ("doom.tex",
    r"\def\dtNoiseAlert{%" + "\n" + r"  \ifdt@snd \ifnum\NSEC>0 \dt@sndgo \fi\fi}",
    r"\def\dtNoiseAlert{\relax}"),
 # The flood ignores whether a line is still open, so sound walks through
 # shut doors and every monster on the map wakes on the first shot.
 "sound-goes-through-doors": ("doom.tex",
    r"  \ifnum\dtNDO>\dtNDP" + "\n" + r"    \ifcsname dtLQ#1\endcsname",
    r"  \ifnum\dtNDO>-99999" + "\n" + r"    \ifcsname dtLQ#1\endcsname"),
 # ---- M21: weapons and the z half of sight -------------------------------
 # Every weapon comes round in one frame, i.e. the world M20 was in: the BFG
 # and the chaingun become the same weapon and nothing about a single shot
 # looks any different.
 "weapons-have-no-cooldown": ("doom.tex",
    r"    \dtWCOOL=\dtGunDelay{\dtWEAP}\relax",
    r"    \relax"),
 # The cooldown is set but never comes down, so the first shot of the level
 # is the last one.
 "cooldown-never-expires": ("doom.tex",
    r"  \ifnum\dtWCOOL>0 \advance\dtWCOOL by-1 \fi",
    r"  \relax"),
 # Pellets are aimed once and all land on the same thing, which is M15's
 # shotgun: the right count and the right damage in the wrong place.
 "pellets-share-one-aim": ("doom.tex",
    r"  \dtnorm\dtSHV{\dtSHY+\dtSHW}%",
    r"  \dtnorm\dtSHV{\dtSHY}%"),
 # ...and the other way round: the first shot of a pistol burst spreads too,
 # so nothing the player points at is ever hit squarely.
 "no-shot-is-ever-accurate": ("doom.tex",
    r"    \ifnum\dtGunAcc{\dtWEAP}=1 \ifnum\dtREFIRE=0 \dtSHZ=0 \fi\fi",
    r"    \relax"),
 # Sight goes back to being two-dimensional, so a monster sees you over a
 # parapet -- which no map in the IWAD can show and only a fixture can.
 "sight-ignores-height": ("doom.tex",
    r"            \ifnum\dtSTJ<\dtSTH \dtSEE=0 \fi" + "\n"
    r"            \ifnum\dtSTJ>\dtSTW \dtSEE=0 \fi",
    r"            \relax"),
 # The one that says \dtMSELF is what keeps a missile off its own launcher.
 # See INTERNALS section 11: it reddens nothing, and the reason is worth
 # knowing rather than deleting.
 "missile-forgets-its-owner": ("doom.tex",
    r"  \dtMSELF=\csname dtJo\the\dtJI\endcsname\relax",
    r"  \dtMSELF=-1 "),
 # ---- M22: the viewport, the pipe, and the tools -------------------------
 # \DTASC implemented the obvious wrong way round: shrink the RENDER to the
 # viewport rather than filter the render into it.  The terminal is then
 # pixel for pixel what it is now and the game plays identically -- the
 # recording is simply four times smaller in each axis, which nothing that
 # looks at the terminal can see.  This is the mutation the headline check
 # exists for.
 "viewport-shrinks-the-render": ("play.tex",
    r"  \dtASW=\numexpr\dtVPW*\dtASN\relax",
    r"  \dtASW=\numexpr\dtVPW*\dtASN\relax \SCRW=\dtVPW \SCRH=\dtVPH"),
 # The filter sums and never divides, so every cell above a sixteenth of
 # full brightness saturates.  The picture is still a picture and still
 # tracks the geometry; it is just wrong.
 "viewport-never-averages": ("play.tex",
    r"    \divide\dtASM by\dtASA\relax",
    r"    \relax"),
 # A block one pixel tall instead of 2n, i.e. the top row of each block
 # standing in for all of it.  Same viewport, same shape, no mean.
 "viewport-samples-one-row": ("play.tex",
    r"  \ifnum\dtASY<\dtASV\else \dtASY=0 \advance\dtRY by1\relax \fi",
    r"  \ifnum\dtASY<1\else \dtASY=0 \advance\dtRY by1\relax \fi"),
 # Luma on red alone.  Green carries most of the brightness, so this is
 # wrong everywhere and looks plausible everywhere -- which is the whole
 # reason the check recomputes the ramp from the palette rather than
 # comparing the engine to itself.
 "luma-ignores-green": ("play.tex",
    r"    \dtALV=\numexpr(2*(\dt@hx#1#2)+5*(\dt@hx#3#4)+(\dt@hx#5#6))/8\relax",
    r"    \dtALV=\numexpr(\dt@hx#1#2)\relax"),
 # The pipe sends column 0 for every column: the right number of bytes, the
 # right shape, a frame that decodes cleanly and is not the frame.
 "pipe-sends-one-column": ("play.tex",
    r"    \immediate\write16{\csname dtFC\the\dtCXA\endcsname}%",
    r"    \immediate\write16{\csname dtFC0\endcsname}%"),
 # Not a .tex mutation, and deliberately so.  This is the bug gif.py
 # actually had: the encoder widening its codes one code before the decoder
 # does.  Every frame round-tripped perfectly against a decoder carrying the
 # same mistake and a foreign one refused the file outright, which is the
 # argument for check.py's ungif() being written from the other end of the
 # spec.  If this reddens nothing, that argument has quietly lapsed.
 "gif-widens-a-code-early": ("gif.py",
    r"            if nxt == (1 << size) + 1 and size < 12:",
    r"            if nxt == (1 << size) and size < 12:"),
 # ---- M23: the rest of the sector machinery ------------------------------
 # The bug M23 found while reading rather than while testing: the shade of a
 # screen row and the state of a ceiling mover in ONE array, so marking a
 # crusher on sector n repainted row n.  Two edits, because the array has a
 # writer and a reader and changing either alone is a crash, not a bug.
 "plane-shade-shares-the-ceiling-array": ("doom.tex", [
    (r"\expandafter\xdef\csname dtYR\the\tmpi\endcsname{\the\tmpb}%",
     r"\expandafter\xdef\csname dtRS\the\tmpi\endcsname{\the\tmpb}%"),
    (r"\hx{\numexpr(#3)+\csname dtYR\number\numexpr#1\relax\endcsname\relax}%",
     r"\hx{\numexpr(#3)+\csname dtRS\number\numexpr#1\relax\endcsname\relax}%"),
    ]),
 # A strobe that goes bright and stays there.  The room is lit and nothing
 # about one frame of it is wrong.
 "strobe-never-darkens": ("doom.tex",
    "      \\dt@liteset{\\csname dtBL\\the\\dtZLS\\endcsname}%\n"
    "      \\dtZLC=\\csname dtBD\\the\\dtZLS\\endcsname\\relax",
    "      \\dt@liteset{\\csname dtBH\\the\\dtZLS\\endcsname}%\n"
    "      \\dtZLC=\\csname dtBD\\the\\dtZLS\\endcsname\\relax"),
 # Every strobe on the map in step, which is specials 12 and 13 for
 # everybody: a level full of lights blinking together like a warning sign.
 "strobes-all-start-together": ("doom.tex",
    "    \\ifnum\\dtZLK<12\n      \\dt@specrand{\\dtZLI}{0}%",
    "    \\ifnum\\dtZLK<0\n      \\dt@specrand{\\dtZLI}{0}%"),
 # The slow strobe keeps the fast one's dark time.
 "slow-strobe-is-fast": ("doom.tex",
    "    \\ifnum\\dtZLK=3\n",
    "    \\ifnum\\dtZLK=99\n"),
 # A glow that reaches the bottom and stays there: a light being turned
 # down, once.
 "glow-never-turns": ("doom.tex",
    r"      \expandafter\xdef\csname dtBD\the\dtZLS\endcsname{1}%",
    r"      \relax"),
 # A thing keeps the light it was placed in, so a strobing room strobes
 # round a monster that does not.
 "things-keep-their-first-light": ("doom.tex",
    "  \\ifcsname dtHs\\the\\dtTI\\endcsname\n"
    "    \\csname dtSl\\csname dtHs\\the\\dtTI\\endcsname\\endcsname",
    "  \\ifcsname dtHsnope\\the\\dtTI\\endcsname\n"
    "    \\csname dtSl\\csname dtHs\\the\\dtTI\\endcsname\\endcsname"),
 # Nukage that hurts on every frame rather than every thirteenth: the level
 # clock dropped out of the test.  The player still loses health in a pool
 # and still loses none outside it.
 "floors-burn-every-frame": ("doom.tex",
    r"  \ifnum\dtZLQ=\dtLEVELT",
    r"  \ifnum0=0 "),
 # Every burning floor costs what hellslime costs.
 "every-floor-burns-alike": ("doom.tex",
    r"    \dtZFB=\dt@slimedmg{\the\dtZFA}\relax",
    r"    \dtZFB=\dt@slimedmg{5}\relax"),
 # The suit is collected, counts down, and stops nothing.
 "suit-stops-nothing": ("doom.tex",
    "      \\ifnum\\dtSUIT>0\n        \\ifnum\\dtZFB<20",
    "      \\ifnum\\dtSUIT>99999\n        \\ifnum\\dtZFB<20"),
 # Gold eyes on the status bar and every hit landing as before.
 "invulnerability-is-cosmetic": ("doom.tex",
    r"  \ifnum\dtINVUL>0 \else \dt@damageplayer{#1}{#2}{#3}\fi}",
    r"  \dt@damageplayer{#1}{#2}{#3}}"),
 # A secret that is found again on every frame spent standing in it.
 "secret-found-every-frame": ("doom.tex",
    r"    \expandafter\xdef\csname dtSs\the\dtSECN\endcsname{0}%",
    r"    \relax"),
 # The floor that ends an episode kills the player instead.
 "exit-floor-kills": ("doom.tex",
    r"      \ifnum\dtZFB<\dtHEALTH \else \dtZFB=\numexpr\dtHEALTH-1\relax \fi",
    r"      \relax"),
 "scrollers-stand-still": ("doom.tex",
    r"        +\ifodd\dtLEVELT 3\else2\fi\relax}%",
    r"        +0\relax}%"),
 # ---- M23: teleporters ---------------------------------------------------
 # The first destination on the map rather than the one in the tagged
 # sector.  On a map with one teleporter this is invisible, which is why the
 # fixture carries a decoy.
 "teleport-ignores-the-tag": ("doom.tex",
    "  \\ifnum\\dtTPS=\\dtMVSEC\n    \\PX=",
    "  \\ifnum0=0 \n    \\PX="),
 # A teleporter that takes you from either side, so stepping off the pad at
 # the far end sends you straight back.
 "teleport-works-backwards": ("doom.tex",
    r"  \ifnum\dtFIRED=0 \ifnum\dtXSIDE=0 \ifnum\dtTPDONE=0",
    r"  \ifnum\dtFIRED=0 \ifnum0=0 \ifnum\dtTPDONE=0"),
 # You arrive and then climb, or sink, to where the eye should be.
 "teleport-keeps-the-old-height": ("doom.tex",
    r"    \PZ=\numexpr(\csname dtSf\the\dtTPS\endcsname+\VIEWH)*65536\relax",
    r"    \relax"),
 # ...or arrive facing the way you were walking.
 "teleport-keeps-the-old-facing": ("doom.tex",
    r"      \dtnorm\PA{\csname dtHa\the\dtTPI\endcsname}%",
    r"      \relax"),
 # The BSP cut the line in two and both halves fire.
 "teleport-fires-once-a-seg": ("doom.tex",
    r"    \dtFIRED=1 \dtTPDONE=1",
    r"    \dtFIRED=1 "),
 "teleport-never-telefrags": ("doom.tex",
    "    \\dt@telefrag\n    \\global\\advance\\dtTELEPORTS by1",
    "    \\global\\advance\\dtTELEPORTS by1"),
 # ---- M23: floors that rise ----------------------------------------------
 # The FIRST higher neighbour instead of the nearest one, which on a sector
 # with a single neighbour is the same floor.
 "raise-takes-the-first-floor-up": ("doom.tex",
    "    \\ifnum\\csname dtSf#1\\endcsname<\\dtRZH\n"
    "      \\dtRZH=\\csname dtSf#1\\endcsname\\relax",
    "    \\ifnum\\dtRZH=32767\n"
    "      \\dtRZH=\\csname dtSf#1\\endcsname\\relax"),
 # Doom's crushing floor stops eight short of the ceiling; this one does not.
 "crushing-floor-goes-all-the-way": ("doom.tex",
    r"      \or \dt@raiseceil{8}%",
    r"      \or \dt@raiseceil{0}%"),
 # "...and change" changes nothing: the floor arrives and looks as it did.
 "raise-and-change-changes-nothing": ("doom.tex",
    r"  \ifnum\dtTFUN>10 \dt@raisechange \fi",
    r"  \relax"),
 "turbo-floors-are-not-fast": ("doom.tex",
    "  \\ifnum\\dtTSPD=2\n"
    "    \\expandafter\\xdef\\csname dtEF\\the\\dtMVSEC\\endcsname{1}\\fi\n"
    "  \\ifnum\\dtTFUN>10",
    "  \\ifnum\\dtTSPD=9\n"
    "    \\expandafter\\xdef\\csname dtEF\\the\\dtMVSEC\\endcsname{1}\\fi\n"
    "  \\ifnum\\dtTFUN>10"),
 # A turbo lower that goes all the way down to the neighbour's floor, so the
 # lip Doom leaves round the pit is gone.
 "turbo-lower-forgets-the-eight": ("doom.tex",
    r"           \advance\dtMVX by8 \fi",
    r"           \relax \fi"),
 # A perpetual lift that makes one round trip and stops, i.e. a lift.
 "perpetual-lift-stops-at-the-top": ("doom.tex",
    "    \\ifcsname dtEP\\the\\dtMVSEC\\endcsname\n"
    "      \\expandafter\\xdef\\csname dtES\\the\\dtMVSEC\\endcsname{5}%",
    "    \\ifcsname dtEPnope\\the\\dtMVSEC\\endcsname\n"
    "      \\expandafter\\xdef\\csname dtES\\the\\dtMVSEC\\endcsname{5}%"),
 "stop-line-does-not-stop-a-lift": ("doom.tex",
    "    \\ifnum\\dtTFUN=6\n      %% EV_StopPlat",
    "    \\ifnum\\dtTFUN=99\n      %% EV_StopPlat"),
 # ---- M23: lines that are shot -------------------------------------------
 # The bullet reaches the switch through whatever was standing in the way.
 "shot-lines-fire-through-monsters": ("doom.tex",
    r"  \dtGZD=\ifnum\dtSHI<0 \dtGUNRANGE\else\dtSHD\fi\relax",
    r"  \dtGZD=\dtGUNRANGE"),
 # ...or the line behind the player counts as crossed.
 "shot-lines-fire-backwards": ("doom.tex",
    "    \\ifnum\\dtGZT>0\n      \\ifnum\\dtGZT<\\dtGZD",
    "    \\ifnum\\dtGZT>-99999\n      \\ifnum\\dtGZT<\\dtGZD"),
 "shooting-a-line-does-nothing": ("doom.tex",
    r"  \ifnum\dtNGUNL>0 \dt@shootlines \fi}",
    r"  \relax}"),
 # ---- M23: powers, the tracer, autoaim, the BFG --------------------------
 "powers-never-run-out": ("doom.tex",
    r"  \ifnum\dtINVUL>0 \advance\dtINVUL by-1 \fi",
    r"  \relax"),
 "berserk-is-a-medikit": ("doom.tex",
    r"    \ifnum\dtWEAP=0 \ifnum\dtBERSERK>0 \multiply\dtSHQ by10 \fi\fi",
    r"    \relax"),
 "blur-sphere-does-nothing": ("doom.tex",
    r"  \ifnum\dtSEE=1 \ifnum\dtMTI<0 \ifnum\dtINVIS>0 \dt@monblur \fi\fi\fi",
    r"  \relax"),
 # The revenant's missile flies the straight line M18 gave it.
 "tracer-never-turns": ("doom.tex",
    r"  \ifnum\csname dtJg\the\dtJI\endcsname>-2 \dt@mishome \fi",
    r"  \relax"),
 # ...or turns as hard as it likes, which is a missile that cannot be
 # dodged.  It still arrives, so only the rate of turn can say so.
 "tracer-turns-on-a-sixpence": ("doom.tex",
    r"  \ifnum\dtJQ<-\TRACERTURN \dtJQ=-\TRACERTURN \fi",
    r"  \relax"),
 "autoaim-flies-level": ("doom.tex",
    r"      \dtSHF=\numexpr\csname dtHz\the\dtSHI\endcsname+\dtBODY\relax",
    r"      \relax"),
 # Forty rays, all of them straight ahead.
 "bfg-rays-all-point-one-way": ("doom.tex",
    r"    \dtnorm\dtJB{\dtJH-\dtBFGARC+\dtJJ*2*\dtBFGARC/\dtBFGRAYS}%",
    r"    \dtnorm\dtJB{\dtJH}%"),
 "bfg-rays-do-no-damage": ("doom.tex",
    "  \\dtHurtThing{\\the\\dtJD}%\n  \\global\\advance\\dtBFGHITS by1 }",
    "  \\global\\advance\\dtBFGHITS by1 }"),
 # ---- M23: the key loop --------------------------------------------------
 "weapon-keys-ignore-ownership": ("doom.tex",
    r"\def\dt@weapto#1{\ifcsname dtUW#1\endcsname \dtWEAP=#1\relax \fi}",
    r"\def\dt@weapto#1{\dtWEAP=#1\relax}"),
 "strafe-walks-forward": ("doom.tex",
    "    \\dtTryMove{\\PX-\\fmul{\\dtsin{\\the\\PA}}{#1}}%\n"
    "              {\\PY+\\fmul{\\dtcos{\\the\\PA}}{#1}}%",
    "    \\dtTryMove{\\PX+\\fmul{\\dtcos{\\the\\PA}}{#1}}%\n"
    "              {\\PY+\\fmul{\\dtsin{\\the\\PA}}{#1}}%"),
 # The exit is reached and the played level carries on regardless -- M16's
 # mutation, in the loop M16's fix did not reach.
 "played-level-never-ends": ("play.tex",
    r"    \ifnum\dtEXIT>0 \dtRunning=0 \fi",
    r"    \relax"),
 # ---- M23: the fixed colormap, which needs an IWAD to see ----------------
 "fixed-colormap-skips-the-walls": ("doom.tex",
    r"    \ifnum\dtFIXCM<0 \else \dtWCM=\dtFIXCM \fi",
    r"    \relax"),
 "fixed-colormap-skips-the-planes": ("doom.tex",
    r"  \ifnum\dtFIXCM<0 \else \dtPSM=\numexpr172+\dtFIXCM\relax \fi",
    r"  \relax"),
 "fixed-colormap-skips-the-weapon": ("doom.tex",
    r"    \ifnum\dtCMN>0 \ifnum\dtFIXCM<0 \else \dtBLM=\dtFIXCM \fi\fi",
    r"    \relax"),
 "fixed-colormap-skips-the-sprites": ("doom.tex",
    r"  \ifnum\dtCMN>0 \ifnum\dtFIXCM<0 \else \dtSRM=\dtFIXCM \fi\fi",
    r"  \relax"),
 # ---- M23: wad.tex -------------------------------------------------------
 # The builder clears its scratch column to palette 0, as the opaque
 # composite does, so every hole becomes a black pixel and the bars become
 # a wall.
 "mask-builder-fills-the-holes": ("wad.tex",
    r"      \expandafter\gdef\csname dtZZ0@\the\dtXV\endcsname{--}%",
    r"      \expandafter\gdef\csname dtZZ0@\the\dtXV\endcsname{00}%"),
 # Every thing the loader places claims to stand in sector 0.  Things are
 # still drawn in the right place and on the right floor -- only what asks
 # WHICH sector a thing is in can tell, and that is a teleporter looking for
 # its destination.
 "things-forget-their-sector": ("wad.tex",
    "              \\expandafter\\xdef\\csname dtHs\\number\\numexpr\\NTHING-1\\relax\n"
    "                \\endcsname{\\the\\dtSECN}%",
    "              \\expandafter\\xdef\\csname dtHs\\number\\numexpr\\NTHING-1\\relax\n"
    "                \\endcsname{0}%"),
 # A sector's special read two bytes late, out of its tag.
 "sector-special-read-from-the-tag": ("wad.tex",
    r"          \dtSCG=\dtS{\number\numexpr\dtSEP+26*\dtJ+22\relax}\relax",
    r"          \dtSCG=\dtS{\number\numexpr\dtSEP+26*\dtJ+24\relax}\relax"),
 # ---- the front-end ------------------------------------------------------
 # The palette is whatever hex arrived before the first frame, all of it:
 # three stray digits from the engine make an odd-length string, and the
 # front-end dies before it has drawn anything.
 "palette-takes-every-hex-digit": ("play.py",
    "    raw = bytes.fromhex(digits[:len(digits) // 2 * 2])",
    '    raw = bytes.fromhex("".join(HEXLINE.findall(hexstr)))'),
}


def edits(spec):
    """A mutation's edits as a list of (old, new), whichever way it is written.

    Most are one replacement and are written (file, old, new).  A few need
    two lines changed together -- an array's writer and its reader, say --
    and are written (file, [(old, new), ...]).
    """
    if len(spec) == 2:
        return list(spec[1])
    return [(spec[1], spec[2])]

def verify():
    """Does every mutation still find the text it names?

    A mutation whose target has moved prints MUTATION DID NOT APPLY and then
    nothing goes red -- which reads, at a glance, exactly like a mutation
    that ran.  It is the silent failure this whole file exists to hunt, and
    it happened here: M16 rewrote \\dt@tagdo from an \\ifnum to an \\ifcase
    and `fires-every-mover' stopped applying, unnoticed, because the run it
    belongs to takes half an hour and nobody reads the top of it.

    This costs no compiles at all, so run it after moving any line a
    mutation names -- which is to say, after moving any line.
    """
    src = {}
    bad = []
    for name, spec in MUTS.items():
        f = spec[0]
        if f not in src:
            src[f] = open(os.path.join(SRC, f), encoding="utf-8",
                          errors="replace").read()
        for old, _new in edits(spec):
            n = src[f].count(old)
            if n == 0:
                bad.append((name, f, "target is gone"))
            elif n > 1:
                # replace(..., 1) takes the first, so more than one match
                # means the mutation is ambiguous and may be hitting a
                # comment.
                bad.append((name, f, f"{n} matches -- ambiguous"))
    print(f"{len(MUTS)} mutations declared")
    for name, f, why in bad:
        print(f"  {name:28s} {f:9s} {why}")
    if not bad:
        print("  every one still finds exactly its target")
    return 1 if bad else 0


def apply(name, d):
    """Copy the tree into `d' and make the one edit `name' names.

    Returns True if it applied.  Separate from main() so a harness can build
    a mutant tree without also running the whole suite in it -- driving a
    subset of the checks against one mutation is how you iterate on a new
    one without waiting out a full pass per attempt.
    """
    f = MUTS[name][0]
    if os.path.exists(d):
        shutil.rmtree(d)
    os.makedirs(d)
    for fn in SOURCES + IWADS:
        p = os.path.join(SRC, fn)
        if not os.path.isfile(p):
            continue
        if fn in IWADS:
            # An IWAD is fifteen megabytes and there is one of these trees
            # per mutation, so it is linked where the filesystem allows and
            # copied only where it does not.
            try:
                os.link(p, os.path.join(d, fn))
                continue
            except OSError:
                pass
        shutil.copy(p, os.path.join(d, fn))
    # newline="" both ways: the edit must not also rewrite every line ending
    # in the file, or the mutant differs from the engine in ten thousand
    # places and the one that matters is not the one a diff shows.
    path = os.path.join(d, f)
    s = open(path, encoding="utf-8", errors="replace", newline="").read()
    for old, new in edits(MUTS[name]):
        old = old.replace("\n", "\r\n") if "\r\n" in s else old
        new = new.replace("\n", "\r\n") if "\r\n" in s else new
        if old not in s:
            return False
        s = s.replace(old, new, 1)
    open(path, "w", encoding="utf-8", newline="").write(s)
    return True


def main():
    if "--verify" in sys.argv:
        return verify()
    only = [a for a in sys.argv[1:] if not a.startswith("-")] or list(MUTS)
    for name in only:
        # Under build/ with everything else a run leaves behind, so one
        # ignore rule and one `check.py --clean' cover the lot.
        d = os.path.join(SRC, "build", "mut", name)
        if not apply(name, d):
            print(f"{name:22s} MUTATION DID NOT APPLY")
            continue
        # sys.executable, not "python3": the name is not on the path
        # everywhere (Windows installs python.exe alone), and running the
        # mutation under a different interpreter from the one driving it is
        # a difference nobody wants to debug from a mutation table.
        r = subprocess.run([sys.executable, "check.py", "--quick"], cwd=d,
                           capture_output=True, text=True, timeout=7200)
        fails = re.findall(r"\[FAIL\] ([^\n]+)", r.stdout)
        print(f"\n=== {name}: {len(fails)} failed")
        for x in fails:
            print("      -", x.strip())
        if not fails:
            print("      !!! NOTHING WENT RED !!!")
    return 0


# Guarded, and it was not.  Everything above used to run at import, so
# `import mutate' -- which is the obvious way for a harness to read MUTS, and
# the obvious way to drive one mutation against a subset of the checks --
# silently started a full pass instead: 78 mutations, a whole `check.py' each,
# hours of it, with no output until the first one finished.  A module worth
# importing needs this line.
if __name__ == "__main__":
    sys.exit(main())
