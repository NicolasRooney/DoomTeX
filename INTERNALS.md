# DoomTeX internals

The engineering notes for [DoomTeX](README.md): how the engine is laid out,
why each part is shaped the way it is, what every TeX trap cost, and what the
regression harness is actually guarding. It was written as the project went
along, milestone by milestone -- M1 is point location and M23 is the last of
Doom's sector machinery -- so §7, §8 and §11 read as a history, and the
earlier a passage is the more it describes a problem that has since been
solved. §1 to §6 are kept current and are the place to start.

Everything here is pure pdfTeX. No shell escape, no LuaTeX, no packages, no
external programs. If you find yourself reaching for one, there is almost
always a pdfTeX primitive that does the job -- `\pdffiledump`, `\pdfstrcmp`,
`\pdfliteral`, `\pdfobj` have covered every need so far.

Verified on pdfTeX 3.141592653-2.6-1.40.25 (TeX Live 2023) and on MiKTeX
25.12, under **both** `pdftex` and `pdflatex` -- see §10.

---

## 1. Run this first

```
python3 check.py
```

Compiles every mode and asserts the invariants below. **Run it after every
change.** Several bugs in this project produced a PDF that compiled with zero
errors and looked plausible; only these invariants caught them.

A run copies the engine into `build/` when it starts and compiles every
fixture there, so nothing it writes lands beside the sources and editing
`doom.tex` while it runs no longer changes the file pdfTeX is half way
through reading -- which it used to, and §11 records what that cost.
`python3 check.py --clean` removes `build/`.

352 checks with an IWAD present and 222 without, plus two for the benchmark
unless `--quick` skips it. The load-bearing ones:

| invariant | why it catches things |
|---|---|
| every emitted column is exactly `\DTH` bytes | front-to-back clipping fills each column exactly once; also the strongest aggregate check on `\dtSplice` |
| zero void pixels | ramp 240 is the filler for a column that never closed; on a sealed map it must never appear |
| WAD render == built-in render | two independent paths into the same arrays (hand-written `\dtAdd*` vs. parsed binary) must agree |
| a composited texture matches an independently composited one | the only check on the composite *itself* rather than its effect: a wrong `PNAMES` index, a patch at the wrong origin or a post decoded one byte out all render a perfectly plausible wall. Three probes, because a single-patch texture at origin (0,0) cannot see a patch-origin bug at all |
| every texture a sidedef names is composited | MAP01 spells `doortrak` and `support2` in lower case and `TEXTURE1` spells both upper, so a byte-exact name match silently loses exactly those two and the surfaces fall back to bricks |
| walls draw real artwork, not a shade ramp | a procedural wall is a ramp base plus 0..13 and can only land on rows that really are ramps; artwork has no such obligation. The `\DTNOTEX` control is what makes this about the walls, and `\DTNOFLAT` on the fixture is what keeps the floors from answering for them |
| planes draw real artwork, not a shade ramp | the same argument for floors and ceilings, and it needs its *own* fixture with the walls forced procedural: written against ordinary geometry it reads the walls and stays green with the flat path disabled entirely |
| a flat matches an independently read one | the only check on the 4096 bytes rather than on how they look. Probes must be asymmetric, and that is asserted: `dtFL` is `[row][column]`, and transposing it mirrors every floor about its diagonal, which a symmetric tile cannot show |
| the camera x/y feeds the flat sampling | forces the sample origin to a camera-independent constant. Adding an offset instead would pass with `\PX` deleted -- it would only prove the term is read |
| planes shade with distance, not sector light alone | forces the falloff to zero. Counting colours does not say this: 30 flats across 59 sector lights reach 105 with the depth term gone |
| every sky column is a vertical strip of SKY1 | recovers which texture column each screen column shows, by search rather than by recomputing the angle, so it stays independent of the engine's arctangent |
| walking does not move the sky, turning does | Doom's sky has no perspective at all. Compared byte-wise on the pixels a stubbed-`\SkyRun` control render marks as sky, so neither half has to guess |
| a sidedef row offset slides the texture | MAP01's 38 row-offset sidedefs all lie off the scripted walk, so this forces the value; without it the whole `\dtVOFF` term could be deleted with nothing going red |
| subsector lookup returns the right sector | the renderer would still draw *something* with a wrong sector; only comparing against known room bounds catches it |
| view height settles on floor + 41 | proves the floor follow converges rather than merely moving |
| a move cannot tunnel through a wall | testing where a move *lands* is not enough; see §7 |
| turning against walls never escapes the map | 120 steps dragged along every wall at every angle |
| splice replaces the right band | the column-length check catches a lossy splice in aggregate; this pins the exact contents |
| sprite hidden behind a closed portal | a shut door draws as a wall but must also record its depth |
| a rising door reveals a sprite gradually | one depth per column cannot see a portal that only narrowed; both halves matter, since monotone alone would pass a step function |
| a raised floor hides a sprite behind it | nothing closes a column here, so this is entirely on the silhouette records |
| every column exactly H bytes, sprites on | the strongest check on the resampler: every post splices back exactly the rows its run emitted |
| arctangent is exact on the compass points | the eight directions and tan(22.5) must land on 0, 1024, ... exactly, or rotations pick up a bias that reads as "monsters face oddly" |
| rotation 1 faces the viewer, the rest anticlockwise | getting the order wrong turns every monster to face the same way, which looks plausible in a render |
| more pictures than thing types | equal counts mean only rotation 0 was found |
| the status bar does not move while the geometry under it does | both halves matter: a bar that failed to draw would also be "identical" if the player stood still |
| sprites draw real artwork, not billboards | a billboard is one ramp row shaded 0..13 and cannot produce colours from four palette rows |
| transparency shows through between posts | one band per column could not leave a gap |
| a shut door blocks the player | proves the headroom test reads the animated ceiling |
| shading stays inside real palette ramps | catches surfaces based on a palette row that is not actually a ramp (§6) |
| using a door is the only way past it | the one end-to-end path through `\dtUse`; a control walk with no use key must stay put, or the check proves nothing |
| `e` in the key loop compiles clean | the reported crash was in `\DTPLAY` on the use key, and `w`/`d`/`q` never reach `\dt@usenear` |
| a held use key does not oscillate its own door | pins the `usedown` latch; without it the scripted walk reverses its door every frame |
| a shut door draws on the door ramp, not the wall's | door surfaces sit on rows the built-in map never uses, so a hit can only have come from the selection in `\dtSegDraw` |
| pdflatex output is pixel-identical to pdftex | the whole of §10's promise, and it was quietly false for the entire life of the HUD. Every other check runs under `pdftex` only, so nothing else can see a bug that depends on the format's register allocator |
| interactive mode typesets nothing | the leak in §8 produced zero errors and a plausible frame; only counting Overfull warnings saw it. Needs a WAD *and* interactive mode, since the batch path renders inside a box |
| standing still, the world still animates | with the player held still nothing else can change a pixel, so a difference can only be the frame letter |
| with the tick stubbed the same walk is frozen | the control half: a sprite pass that had stopped drawing would also be identical every frame |
| the frame picker walks a sequence that doubles back | `ABCDCB` is not a run; a picker that assumed base+n asks for `E` and silently falls back to rotation 0 |
| the modulus truncates rather than rounding | `\numexpr` would round 5/6 to 1 and advance every sequence a tic early once per cycle |
| two things of one type are out of phase | without the per-thing offset every torch in a room flickers in lockstep |
| crossing a line opens the sector its tag names | the trigger is two rooms away from the door it opens, so nothing about the geometry of the line that fired can stand in for the tag. Both halves matter: the control keeps the shut room *and* the tags and drops only the special |
| a tagged trigger moves every sector carrying that tag | MAP01's switch 84 names four sectors, and three of them staying put is invisible to any aggregate count |
| and moves no sector carrying a different one | sector 21 is a marked, movable sector with another tag, which is what tells "matched the tag" from "started every mover" |
| a once-only line does not fire a second time | the floor is put back by hand and the line pressed again, which is the only way to see a spent flag that is never read |
| pressing a switch swaps its face to the pressed one | asks the engine for the index of `SW2BRCOM` *by name*, so a swap landing on any other texture fails; and separately that the pressed face composited, since an unresolved one falls back to bricks and would still look like a change |
| a lift lowers the floor under a player standing still | with the player held still nothing else can move a floor |
| and the eye rides it down | `\dtFollowFloor` is reached only from `\dtTryMove`, so this was false for the whole first draft: the floor dropped away and the camera stayed put, with no error and a perfectly steady frame |
| a medikit is refused at full health and left standing | the rule lives in the *return value*, not in the arithmetic: without it the thing vanishes having healed nothing. The pair matters -- the same medikit on the same floor at 50 health must go |
| a thing that is taken stops being drawn | and its control is the whole of what makes it mean anything. This must be checked **standing still**: after a forward walk the thing is behind the camera, so "no pixels of it" is what a working engine, a broken one and an empty room all report alike (§11) |
| a thing out of reach overhead is not collected | the height half of Doom's box test. Same footprint, 60 units up. Nothing on a stock map is arranged to show this, so it needs a fixture |
| a backpack doubles the maxima once and only once | doubling per pickup is the obvious bug and it is invisible on every map that carries one backpack |
| the pain division truncates rather than rounding | at exactly 80 health `(100-80)*5/101` rounds to 1 and truncates to 0, so `\numexpr` wounds the face a point early, on one health value in five |
| health digits are right-aligned on the width of the digit ZERO | `STTNUM1` is three pixels narrower than the other nine and Doom still advances by 14. The expected gap is read out of the WAD rather than written down, so the check is a statement about Doom's data |
| the ammo count reads the weapon in hand | not a fixed slot: picking up the shotgun has to change which number the bar is reading, and the fist must show none at all |
| line of sight agrees with an independent one | check.py walks the map's `LINEDEFS`, the engine walks its `SEGS`; the segs partition the linedefs exactly, so a disagreement is a real bug in one of the two. The probes are the map's own -- the player start to each of MAP01's 29 monsters -- and the check asserts they are not all one answer, or the comparison would be vacuous |
| a monster wakes, closes and shoots | three separate readings of one fixture, because a monster that woke and did not move and one that never woke look the same in a health count |
| ...in Doom's own steps | every amount a zombieman can take off is `((rand%5)+1)*3`, i.e. a multiple of three in 3..15. A damage path that had lost the multiplier, the `+1` or the modulus would still lower health |
| ...and none of it happens to a thing with no state machine | the control is the same thing at the same spot **unspawned**, which is tighter than `\DTNOMON`: identical geometry, identical sprite, no tick |
| firing kills a monster and counts it | the whole player-to-monster path: aim, sight, damage, death |
| and every shot costs a bullet | Doom spends the bullet whether or not the shot hits, so the whole run is compared rather than the end of it |
| a dead monster ends as a corpse, and is not killed twice | state 5 is never ticked and never aimed at; without the second half the kill count runs away while the trigger is held |
| a live monster blocks the player | on the **built-in** map, where a walk that stops can only have been stopped by the monster. On MAP01 the corridor stops it at the same place |
| a dead player stops walking and the view drops to the floor | `P_DeathThink`, both halves; the eye eases to floor + 6 at the rate `\dtFollowFloor` already uses |
| armour absorbs its share, and stops being armour when it runs out | the class is cleared, not merely the points -- and 2 points of green against a 30-point hit is what tells those apart |
| the face turns toward whatever hit it | and an ouch above 20 damage, and death outranking a wound that landed on the same frame: `ST_updateFaceWidget` is a priority list and the order is the whole of it |
| a crusher comes down to the floor and eight units | and *goes back up and comes down again*, counted as changes of direction: one descent is a ceiling-lower, and what makes it a crusher is the second |
| a closing ceiling crushes the player under it | in Doom's tens, with the control being the same walk under a ceiling that is not moving -- not a different route |
| a stop line halts it | and the pair is what says so: the same crusher, unstopped, is still moving over the frames the stopped one holds still for |
| a staircase raises each step above the one before | 8, 16, 24 out of floors 0, 24, -16 -- the accumulation along the chain, not three sectors all raised by 8 |
| 'to the brightest neighbour' reads the neighbour | room 1 ends on room 0's own 224, which is a number out of the map rather than one written into the engine |
| crossing the exit line ends the level | against a control that runs to `\NFRAMES`; a level that finished and one that ran out of frames are different things |
| the arms panel reads what you own, not what is in your hands | `STARMS` is a background and draws either way, so "it appears" says nothing; the subject is the six numbers, and `hudsg` *holds* a shotgun it does not own, which is what tells `dtUW` from `\dtWEAP` |
| a key you are holding shows on the bar, and one you are not does not | the pair, in a box that is empty in the control |
| a backpack moves the maxima beside the ammunition | the only thing on the whole bar that ever moves the right-hand column, so it is the only thing that says that column reads `dtUM` |
| a rocket crosses the room in steps of fifty | the step is asserted, not merely that it moved: the ratio to the player's own 40 is the whole of what makes a rocket read as fast, and `\DTNOMIS` is the control that throws nothing at all rather than throwing something that went nowhere |
| a missile flies over a step it is above, and is stopped by one it is below | and by a ceiling it would not fit under. Three spawns differing only in `z` across one portal: any one alone is also satisfied by a missile that never crossed anything |
| a rocket's blast falls off with distance | forty units further away has to cost the player *less*, and both have to cost something, or it is a check on nothing |
| a fireball arrives at the player rather than in the floor short of them | `\numexpr` rounds, and a z velocity split per substep rather than taken over the frame descends a third too fast. The missile still flies, explodes, animates and draws; only the player's health knows |
| an imp with `\DTNOMIS` cannot reach at all | the control that says the fireball is doing it, and the same run is the proof `\DTNOMIS` puts M15's world back |
| missile slots are recycled and bounded | more explosions than there are slots, *and* `\NTHING` stopping at the cap -- one without the other is either a leak or a cap jammed shut |
| a BFG with thirty-nine cells left does not fire | a hundred cells is deliberately not a multiple of forty, so the run has to stop with twenty; it is the only reading that tells a weapon whose shot costs forty from one that merely draws down faster |
| a masked middle's top edge is where the map's own numbers put it | the opening's floor plus the texture's height when the line is unpegged from the bottom, the opening's ceiling when it is not, plus the sidedef's row offset -- all four read out of the WAD. Getting the pegging backwards moves it by the whole height of the texture |
| the room behind shows through between the bars | several separate runs of changed columns across one row, where an opaque middle would be one. It is the whole feature in a single number, and it needs a texture that *has* holes: MAP01 names two masked middles and one of them is a solid wall hung in a doorway |
| a locked door refuses a player without its key, and opens for one who has it | and a refusal does not spend a once-only line, which is what says the key test happens before `\dt@fireact` rather than inside it |
| a fireball that catches another monster turns it on the thrower | and the imp fights back, and with `\DTNOMIS` neither notices the other -- the control is the same two monsters in the same places with nothing to start it |
| a gunshot wakes a monster that cannot see the player | `\dtSight` is stubbed to fail for every pair, so nothing in the fixture *can* wake by sight; and a shut door stops it, which is what makes it `P_RecursiveSound` rather than "wake the map" |
| a held trigger fires at the weapon's own rate | seven pistol shots against four shotgun in forty frames. The ratio between weapons is the subject, and one shot a frame could not express it |
| a shotgun blast lands on more than one thing | which a single shared aim cannot do however wide the spread numbers are, and the control is that the first shot of a pistol burst is accurate and reaches exactly one |
| a monster cannot see over a parapet it should not | needs geometry built on purpose: every opening on MAP01 reaches its floor, which is why this went unnoticed for two milestones |
| a decimated viewport leaves the render alone | the whole of `\DTASC`. The obvious wrong implementation -- shrink the render, blow the page up -- plays identically and every other check here is about what the terminal shows, so nothing else can see it. Two renders of the same walk at `\DTASC{1}` and `\DTASC{2}`, compared byte for byte |
| a viewport cell is the mean of the block behind it | against a second implementation of the filter, from the PDF's own pixels and the PDF's own palette. Everything else judges the filter by whether the picture still looks like a room, and a wrong filter does. Its control is that a point sample -- the implementation this replaced -- disagrees somewhere, or the check is satisfied by a filter that never averages |
| the viewport is `\DTW/n` by `\DTH/2n`, every frame | a count short by one a frame is what the `\read16` prompt gluing itself to the first row looks like, and it reads as a working viewport |
| the pipe hands over exactly what the PDF gets | the whole claim of `\DTPIPE`: a front-end sees the true frame. Decoded out of the terminal and compared with the decoded pages |
| a wrapped state line is reassembled whole | the state line is longer than `max_print_line` and is hard-wrapped mid-token, so `kill` and `of` -- the last two fields -- are exactly what a reader taking lines one at a time loses, silently, while still parsing perfectly |
| a demo replays frame for frame | the only check that runs the pipe through the front-end built to consume it, and the only statement that the engine is deterministic frame by frame rather than repeatable in aggregate. Its control is the same keys from a different start, which must diverge -- and it has to be the *world* that differs, since `\SCRW` reaches `\PPLANE` and nothing else, so a replay at another resolution matches and proves nothing |
| a GIF decodes back to the render it came from | against a decoder written from the other end of the spec. An encoder and a matching decoder can agree to get the code-width transition wrong together: the decoder runs one code behind, and `gif.py` had that bug -- every frame round-tripped against a decoder sharing the mistake while a foreign one refused the file. Both encodings are decoded, since a walk is all whole frames and only a run that stops exercises the held-frame path |
| the benchmark measures frames, not the load | it used to divide the whole run by the frame count, which §9 spends three paragraphs forbidding, and overstated a frame by 25% on the map with no WAD to parse |
| a strobe is bright for two frames and dark for six, and the slow kind for fourteen | Doom's 5, 15 and 35 tics at two frames to five. The run lengths are asserted, not merely "it changes": a strobe that never darkened and one whose dark time was the wrong special's both still flicker |
| synchronised strobes change on the same frames, and the plain kind do not | specials 12 and 13 are the same thinkers started together, and the pair is the whole of the difference |
| a thing is lit by the room it is standing in now | asked of `\dtThingLight` directly with a placed light of 100, a value no room has, so the answer can only have come from the sector |
| a burning floor hurts every thirteenth frame, by the amount its own special names | three hits of ten in forty frames, and fives and twenties for the other two specials -- "health went down" is also what a floor that burned every frame, or burned everything alike, reports |
| the last floor of an episode ends the level instead of killing you | special 11 clamps its damage to leave one point and exits under eleven; without the clamp the run ends on a corpse |
| a secret is counted when it is walked into, and once | the walk spends six frames inside it |
| marking a ceiling mover does not repaint the planes | the row shade and the mover's state shared one `\csname` array for seven milestones. Asked of `\PlaneRun` on a six-row view, because that is the only height at which a three-sector map has a sector index whose row shade is not already 0 |
| a teleporter puts you on the destination its tag names | with a decoy destination earlier in the thing list, in a sector whose tag does not match -- on a map with one teleporter "the first destination found" is the right answer by accident |
| ...only through its front, at eye height at once, facing the way it faces | three separate ways for an arrival to be subtly wrong, each with its own mutation |
| a teleporter on a real map lands on its tagged destination | the built-in fixtures teleport to things dropped in by hand, which are located on the spot; a thing `wad.tex` places carries the sector the loader recorded for it, and that is the path all four hundred of Doom II's teleporters take. The landing point and the sector's tag are read out of the lumps |
| a floor raised to the next one up stops at the NEAREST above it | the sector that moves is dropped first so that it has two higher neighbours to choose between; with one, "nearest" and "first found" are the same floor |
| a perpetual lift goes down, comes back, and goes down again, and a stop line holds it | counted as waits at an end, as the crusher is counted in turns: one round trip is a lift |
| shooting a gun-activated line opens the door its tag names | and firing the other way does not, and a monster in the way takes the bullet instead -- the line has to be crossed by a shot that got that far |
| a revenant's missile turns in flight, no faster than Doom's tracer can | launched due south at a player to the west. That it arrives says it homes; that no frame turns it more than 240 units says it is a tracer and not a missile that simply knows where you are |
| a player's rocket climbs to a target above it | the z at the impact, against the same shot with `\DTNOAIM` arriving at the height it left at |
| a BFG's rays catch what is in front and to the side, but not what is hidden behind something | four immortal monsters: one takes the ball, one stands directly behind it, one is 34 degrees off, one is outside the fan. The arc this replaced caught the hidden one too |
| invulnerability draws the whole view through the inverse map | every pixel above the status bar that is not sky is one of table 32's thirty-nine greys -- a statement about each pixel on its own, and the control is that the plain view is mostly not those colours |
| posts built from the composite draw the picture the patch's own did | the commercial maps name no masked middle that is both several patches and full of holes, so the builder is handed MIDBARS3, whose right answer is already on the page, and every column is then held against `check.py`'s own composite hole for hole |
| every frame the monster and missile tables name is in the WAD | a wrong letter does not crash: the loader misses, the fallback misses, and the monster is a coloured billboard for exactly the frames nobody watched. Read out of `doom.tex` and held against the lump directory |

If an IWAD (`doom2.wad`, `doom.wad`, `doom1.wad`) is in the directory it is
exercised too, and skipped otherwise. No IWAD is distributed with this project.

---

## 2. What works today

- BSP traversal front to back (`R_RenderBSPNode` / `R_PointOnSide`), with
  early-out when every column is closed.
- One-sided walls, two-sided linedefs as portals with upper and lower
  textures, per-sector floors and ceilings, distance and sector-light shading.
- WAD loading: header, directory, `VERTEXES`, `LINEDEFS` (including specials,
  flags and both sidedefs), `SIDEDEFS` (textures and offsets), `SECTORS`,
  `SEGS` (including the offset along the linedef), `SSECTORS`, `NODES`,
  `THINGS`, `PLAYPAL`, `PNAMES`, `TEXTURE1`/`TEXTURE2`, `COLORMAP`.
- **Real commercial IWADs.** `doom2.wad` MAP01 loads and renders correctly:
  383 verts, 370 lines, 529 sides, 59 sectors, 601 segs, 194 subsectors,
  193 nodes, 61 things after filtering multiplayer-only entries, 4 doors,
  40 composited wall textures (39 named by sidedefs, plus the sky), 30 flats.
- **Real wall textures**: `TEXTURE1` recipes composited from `PNAMES` patches
  at load, sampled per column and shaded through `COLORMAP` on Doom's own
  light table, with Doom's pegging rules, sidedef offsets, seg offsets and
  fake contrast. `\DTNOTEX` keeps the procedural tables instead.
- **Real flats**: `F_START`/`F_END` flats read per map and sampled per plane
  pixel at its world position, shaded by distance on Doom's `zlight` table.
  `\DTNOFLAT` keeps the procedural ramps instead. This retires the
  palette-ramp constraint in §6 outright -- no surface is on a ramp now
  unless a fixture asks for one.
- **Sky**: `F_SKY1` recognised as a marker rather than a flat, drawn as a
  strip of a wall texture picked by view angle, with no perspective and no
  shading. `\DTSKY` chooses the texture (default `SKY1`; Doom II wants
  `SKY2` from MAP12 and `SKY3` from MAP21, which is not derived from the map
  name).
- Point location, floor following, swept collision with wall sliding.
- Things composited after the wall pass, back to front, clipped against
  per-column silhouette records rather than a single depth.
- **Real sprite graphics**: Doom's picture format, read per map from the lumps
  between `S_START` and `S_END`, resampled and spliced post by post, shaded
  through `COLORMAP`.
- Doors: ceiling animation, `P_UseLines`-style activation, reversal on
  re-use with a `usedown` latch for a held key, distinct face and track
  surfaces, and correct interaction with both occlusion and collision.
- **The rest of the sector machinery**: linedef and sector tags, a table
  decoding 51 of Doom's linedef specials into six orthogonal fields, moving
  floors (lifts that return, and floor-lowers that do not), doors opened by
  switch and by walkover as well as by hand, once-only and repeatable
  activation, and switch faces that change from `SW1` to `SW2` when pressed.
  A player standing on a lift rides it. On MAP01 that is 7 doors, 5 moving
  floors and every special the map carries except the exit switch.
- **Frame animation**: per-type frame sequences on Doom's own tic counts,
  phase-staggered per thing, so torches flicker and monsters shift their
  weight.
- **Pickups, and a status bar that reads them.** A type-to-effect table in
  Doom's own numbers -- health, armour, four ammunition slots with their
  maxima, nine weapons, three keys, the backpack -- collected by a box test
  against the thing list on every move. A thing that is taken stops being
  drawn and stops being collectable, which makes this the first thing here
  that changes the map permanently. The bar shows health, armour and the
  ammunition of the weapon in hand, with Doom's face reacting to how much
  damage the player has taken, and the weapon in your hands is the one you
  picked up.
- **Enemies, and a fight.** A per-thing state machine on Doom's own frames --
  idle, chase, attack, pain, death, corpse -- ticked once a frame. Monsters
  wake on line of sight, close through the same swept collision test the
  player uses but with their own radius, and attack: hitscan for the
  zombieman, the shotgun guy, the chaingunner and the SS, fists for
  everything whose real weapon is a projectile. They are solid, they flinch
  on Doom's painchance, they die into the corpse their death animation ends
  on, and they are counted. The player shoots back with the hitscan weapons
  through a `P_LineAttack`-shaped aim, and can be killed -- which is what
  finally makes the rest of the face reachable: the ouch, the two turns
  toward whatever hit you, the evil grin at a new weapon, the rampage, and
  `STFDEAD0`. `\DTNOMON` keeps the whole map asleep and insubstantial.
- **The rest of the sector types.** Crushing ceilings that come down to the
  floor and eight, go back up and do it again, hurting whoever is under
  them; ceilings that lower to the floor or rise to the highest neighbour;
  a `stop` line that halts one; staircases built out from a tagged sector
  along Doom's own chain rule, accumulating a step each time; the four
  non-strobing light specials; and the level exit, which ends the run and
  says so. Thirty more `\dtActDef` rows, one more state machine, and the
  staircase borrows M13's moving-floor machine outright.
- **Projectiles.** The first thing here that exists without being on the map:
  a missile is spawned by whatever threw it, flies on a velocity, and is
  gone. Seven types out of Doom's `info.c` -- the imp's fireball, the
  cacodemon's, the bruisers', the revenant's, the rocket, the plasma bolt and
  the BFG ball -- each with its own speed, damage, radius and two-state frame
  sequence. They are substepped so a bolt cannot cross a wall between two
  samples, they are stopped by the opening at the height they arrive at
  rather than by a step they cannot climb, they explode against geometry,
  against a monster and against the player, and a rocket's blast falls off
  with distance and catches whoever fired it. That makes the rocket launcher,
  the plasma rifle and the BFG real weapons, and gives the imp, the
  cacodemon, both bruisers and the revenant the ranged attack M15 left them
  without. Missile slots are recycled, so sustained fire is bounded.
  `\DTNOMIS` throws nothing at all.
- **Masked middle textures.** A middle texture on a two-sided line drawn with
  its holes in, so the room beyond shows through the bars. It cannot be drawn
  during the wall pass -- a column fills from the outside in and the middle
  of a portal is the gap the traversal has not reached -- so it is recorded
  as a drawseg and composited in the sprite pass, against the same per-column
  silhouette a sprite is clipped to. Single-patch textures keep their patch's
  post chain beside the opaque composite; `\DTNOMASK` keeps every middle
  opaque.
- **Locked doors, infighting and sound.** Doom's six locked door specials and
  six locked switches demand their key, and a refusal spends nothing; a
  monster caught in another's crossfire turns on it, because `P_DamageMobj`
  repoints the victim's target at whatever hurt it and the tick now reads
  that target rather than always the player; and a gunshot floods
  `P_RecursiveSound` through the sectors, round corners a sight line cannot
  turn and stopping at any line whose opening has closed. `\DTNOLOCK` and
  `\DTNOSND` are the off switches.
- **A weapon state machine, and Doom's spread.** Each weapon has its own
  refire period out of Doom's tics, so the chaingun and the BFG stop being
  the same weapon; every shotgun pellet is aimed separately, so a blast lands
  on more than one thing; and the pistol and chaingun spread only once the
  trigger has been round, which is `P_GunShot`'s `accurate` argument.
  `\DTNOWAIT` restores one shot a frame.
- **The height half of `P_CheckSight`**: a sight line now has to pass through
  each opening at the height it arrives at, so a monster cannot see over a
  parapet. `\DTNOZ` drops it.
- Interactive mode: `\read16` loop, ASCII to the terminal, `w/s/a/d/e/f/q`.
- **A viewport that is not the render.** `\DTASC{n}` makes a terminal cell
  cover `n` by `2n` framebuffer pixels -- the 2 being the cell's own aspect,
  which this file used to spend by printing every other row and discarding
  the rest -- so `\DTW`/`\DTH` are free to be the resolution you want the
  *recording* at while the terminal stays the size a terminal is. A cell is
  the mean of the block behind it, in luma, and the render never learns that
  anything was decimated. `\DTASC{4}` on a 304x240 render plays on the same
  76x30 terminal a 76x60 render always did, and records four times the
  picture in each axis.
- **A frame channel for a front-end.** `\DTPIPE` hands the framebuffer over
  as hex between markers instead of drawing it, and `play.py` is what reads
  it: single keypresses with no Enter behind them, arrow keys, the frame in
  colour at twice the vertical resolution one character per pixel can manage,
  and demos that record and replay. It is *cheaper* than drawing the ASCII
  (§9), so the engine pays nothing for the front-end.
- **Tools.** `gif.py` turns a recording into an animated GIF with no
  resampling and no quantisation anywhere -- palette indices in, the same
  indices out, and the palette read from the PDF rather than passed in.
  `dump.py` reads the palette from the PDF now too.

- **Sector specials** (M23). The thing a sector IS rather than what a line
  does to it: Doom's five light thinkers -- the random flash, the two strobes
  and their synchronised twins, the glow and the fire flicker -- on Doom's
  own tic counts; the four burning floors, on the level's clock, with the
  radiation suit; the episode-ending floor; and secrets, counted once.
  Linedef special 48 scrolls its wall. `\DTNOSPEC` switches all of it off.
- **Teleporters, rising floors and shot lines** (M23). Kind 7 moves the
  player to the destination thing in the tagged sector, from the front only,
  and telefrags; kind 8 raises a floor to a target settled when the line
  fires; kind 2 gained the turbo lower, the perpetual lift and its stop
  line; and `act` 4 is a line fired by a hitscan crossing it. 130 linedef
  specials are declared, which is every kind `doom2.wad` uses but three.
- **Powers** (M23). Four timers, each read in one place, and a fixed
  colormap -- `\dtFIXCM` -- that draws the whole view through table 1 for the
  visor or the inverse map for invulnerability at no per-pixel cost.
  Invisibility swings the aim of whatever is shooting at you; berserk
  multiplies the fist by ten. `\DTNOPOW`.
- **The tracer, autoaim and the BFG's rays** (M23). The revenant's missile
  homes at Doom's turn rate (`\DTNOHOME`); a player's missile is aimed
  vertically at whatever the aim scan finds (`\DTNOAIM`); and the BFG's spray
  is forty rays, each stopping at the first thing it meets.
- **Four more monsters** (M23): the mancubus, the arachnotron, the cyberdemon
  and the spider mastermind, which are table rows and two more missile types.
- **Masked middles of any number of patches** (M23), built column by column
  from the composite where a single patch could not supply the posts.
- **A played level that ends** (M23): the exit stops the key loop as it stops
  the batch one, both print a tally, and the key loop gained strafing and
  Doom's number keys.

Not implemented: the arch-vile and the pain elemental, which are still
scenery, as are Keen and the boss brain; the timed doors (linedef 16 and 76,
sector specials 10 and 14), the donut, the lower-and-crush ceilings and the
monster-only teleporters; the tag-666 effects; the computer map, for want of
an automap to fill in; and carrying the player's state from one level into
the next.

**The string pool is now a design constraint and belongs at the front of your
mind, not in a footnote.** See §6. Every texel array in this project is
packed eight to a control sequence because one texel per control sequence
does not fit; anything you add that wants a per-texel array has the same
problem and needs the same answer.

---

## 3. Files

| file | contents |
|---|---|
| `doom.tex` | engine: fixed point, trig, palette, texture, map arrays, built-in level, BSP renderer, point location / movement, things, doors, PDF output, main loop |
| `wad.tex` | WAD parser; fills exactly the same arrays as the built-in level |
| `play.tex` | interactive mode: the viewport box filter, the `\DTPIPE` channel, key loop |
| `test.wad` | real PWAD, three rooms, hand-built BSP, two portals |
| `maketest.py` | regenerates `test.wad` from the built-in map |
| `check.py` | regression harness |
| `mutate.py` | breaks the engine on purpose, one bug at a time, and reports which checks notice |
| `dump.py` | print a frame from a PDF as ASCII, by shade or by ramp; or as a PNG |
| `play.py` | front-end: raw keys in, the frame in colour out, demos recorded and replayed |
| `gif.py` | a recording to an animated GIF, indices in and the same indices out |
| `build/` | not in the repository: `check.py` makes it, copies the engine in, and writes every fixture, log and PDF there. `mutate.py` puts its mutant trees under `build/mut/` |

`play.py` and `gif.py` are supplements, not dependencies: the engine plays
and records with neither of them present, and nothing in `doom.tex`,
`wad.tex` or `play.tex` knows they exist. Both are pure standard library, on
the same principle as the rest of the project. The only coupling is the
`\DTPIPE` protocol, which is four markers and is documented in §5.

`test.wad` is **not** an arbitrary fixture: check.py renders it and diffs the
result against the hand-written `\dtBuiltinMap`, so it has to describe the same
level.  `maketest.py` regenerates it from that map if it goes missing.  Two
things make the comparison work and are easy to break: sectors must keep their
order, because a WAD does not store ramps and wad.tex assigns them from
`\dtRampW/F/C` indexed by sector mod 11; and the root node must be last,
because the WAD path sets `\DTROOT` to `\NNODE-1`.

`doom.tex` reads top to bottom: configuration, fixed point, trig, hex, palette,
texture, map arrays, built-in level, renderer, BSP, point location / movement,
things, doors, PDF output, main.

---

## 4. Data model

All map data lives in `\csname` arrays, so size is bounded by hash and main
memory rather than by register count. Index is decimal, appended to the prefix.

```
dtVx<i> dtVy<i>            vertex x, y (map units, integers)
dtLF<i> dtLB<i>            linedef front/back sector; -1 = one-sided
dtLP<i>                    linedef special; ABSENT means none (\dtLineSpec)
dtSf<i> dtSc<i>            sector floor / ceiling height
dtSl<i>                    sector light 0..255
dtSw<i> dtSp<i> dtSq<i>    sector wall / floor / ceiling palette ramp
dtGa<i> dtGb<i>            seg start/end vertex index
dtGl<i> dtGs<i>            seg linedef index, side (0 = front)
dtGn<i>                    seg length in map units (texture u, and collision)
dtCn<i> dtCf<i>            subsector seg count, first seg
dtNx<i> dtNy<i>            node partition origin
dtNu<i> dtNv<i>            node partition delta
dtNr<i> dtNl<i>            node right/left child; >= 32768 means subsector
dtHx<i> dtHy<i>            thing x, y
dtHt<i> dtHz<i>            thing type, floor height of its sector
dtTS<x> dtBS<x>            per-column accumulated top / bottom pixel spans
dtFC<x>                    assembled column, ready to splice and emit
dtVI<k> dtVD<k>            visible-thing index and scale, rebuilt each frame
dtHl<i>                    light of the sector thing i stands in
dtZT<x> dtZB<x>            per-column silhouette: {scale}{row} pairs, near to far
dtSN<type>                 thing type -> sprite key (4 chars + frame letter)
dtPw<k> dtPh<k>            sprite width, height
dtPl<k> dtPt<k>            sprite left and top offset
dtQ<k>@<col>               source column: {topdelta}{length}{pixels}..., {-1} terminated
dtPf<k>                    present when this rotation is stored mirrored
dtHa<i>                    thing i's facing, 8192 to a turn
dtAT<t>                    arctangent: angle 0..1024 for tangent t/1024
dtLV<HH>                   luma 0..255 for palette byte HH (play.tex)
dtCM<light>@<HH>           COLORMAP: shaded byte for source byte HH at light 0..31
dtDT<s> dtDS<s> dtDW<s>    door: is-door flag, state, wait counter
dtDC<s> dtDO<s>            door: shut ceiling, open ceiling
dtDX<k>                    door k's sector
dtDA<s> dtDF<s>            door: 1 = wait and close, 0 = stay; fast if present
dtLT<i>                    linedef tag; ABSENT means 0 (\dtLineTag)
dtSt<s>                    sector tag; ABSENT means 0 (\dtSecTag)
dtLA<n>                    linedef special n -> its six action fields
dtLU<i>                    present once a once-only line has been spent
dtET<s> dtES<s> dtEW<s>    moving floor: is-mover flag, state, wait counter
dtEL<s> dtEH<s>            moving floor: low target, high target
dtER<s> dtEF<s>            moving floor: 1 = comes back up; fast if present
dtEX<k>                    moving floor k's sector
dtXs<t>                    texture t <-> its pressed/unpressed switch twin
dtMA<type> dtMN<type>      animation: frame letters, and how many
dtMT<type>                 animation: frames each letter holds
dtT<u>@<v>                 wall texture shade offset, 64 x 65
dtD<u>@<v>                 door texture shade offset, same 64 x 65 shape
dtYs<i>                    sidedef i's sector
dtYu<i> dtYl<i> dtYm<i>    sidedef upper / lower / middle texture; -1 = none
dtYx<i> dtYy<i>            sidedef x and y texture offset
dtLR<i> dtLL<i>            linedef i's front / back sidedef; -1 = none
dtLG<i>                    linedef pegging: 1 = DONTPEGTOP, 2 = DONTPEGBOTTOM
dtGo<i>                    seg i's offset along its linedef
dtXi<namehex>              texture name (uppercased) -> index
dtXw<t> dtXh<t>            texture width, height; width 0 = never resolved
dtXm<t>                    texture v wrap, i.e. height * 65536
dtXo<t>                    file offset of the maptexture_t record
dtXp<i>                    PNAMES entry i, as uppercased name hex
dtXq<namehex> dtXl<...>    patch wanted / patch lump offset
dtX<t>@<g>@<v>             EIGHT composited texels, {..}{..} x8, at u = 8g..8g+7
dtZZ<k>@<v>                compositing scratch: 8 columns, reused per group
dtFi<namehex>              flat name -> index
dtFw<t>                    1 once the flat's 4096 bytes were read; 0 = fell back
dtFL<t>@<v>@<g>            EIGHT flat texels, {..}{..} x8, at u = 8g..8g+7
dtAf<s> dtAc<s>            sector floor / ceiling flat; -1 = none, -2 = sky
dtYS<y>                    PPLANE*512/(2|y-HALFH|+1): the row half of a plane's depth
dtCC<n>                    clamp light level n-128 into the loaded COLORMAP
dtKA<x>                    angle of column x's ray away from the view centre
dtSV<y>                    sky texture row for screen row y
dtIK<type>                 item kind; ABSENT means the type is not collectable
dtIA<type> dtIB<type>      the item's two payload fields
dtHG<i>                    present once thing i has been taken
dtUA<k> dtUM<k>            ammunition held / maximum, k = 0..3
dtUW<n> dtUK<n>            present if weapon n / key n is held
dtUT<n> dtUS<n>            weapon n's ammo slot (-1 = none) and its picture
dtGq<namehex>              a status-bar picture wanted by name (wad.tex)
dtOs<i> dtOh<i>            monster state, health remaining
dtOc<i> dtOf<i>            tics left on this frame, and which letter it is
dtOq<t> dtOr<t> dtOp<t>    per type: spawn health, radius, speed
dtOw<t> dtOe<t> dtOm<t>    per type: painchance, melee dice, melee multiplier
dtOg<t>                    per type: hitscan shots per attack
dtOa<t>@<s> dtOn<t>@<s>    state s's frame letters, and how many
dtOd<t>@<s>                state s's tics per letter
dtRT<s> dtRS<s>            ceiling mover: is-mover flag, state
dtRL<s> dtRH<s>            ceiling mover: low target, high target
dtRC<s> dtRF<s>            ceiling: present if a crusher / if fast
dtRX<k>                    ceiling mover k's sector
dtSU<s>                    present once a sector is part of a staircase
dtJs<i>                    missile state: 0 flying, 1 exploding, 2 spent
dtJf<i> dtJc<i>            missile: frame letter index, tics left on it
dtJu<i> dtJv<i> dtJw<i>    missile velocity, map units per FRAME
dtJo<i>                    who threw it: -1 the player, else a thing index
dtJp<t> dtJd<t>            per type: speed (per TIC), damage multiplier
dtJr<t> dtJb<t> dtJy<t>    per type: radius, splash damage, sprays if 1
dtJm<t>                    the missile THING TYPE t puts into the world
dtJa<t>@<s> dtJn<t>@<s>    state s's frame letters, and how many
dtJt<t>@<s> dtJk<t>@<s>    state s's tics per letter, and its SPRITE NAME
dtXk<t> dtXn<t>            texture wanted as a masked middle / posts kept
dtQMK<t>@<col>             those posts, in \dt@readpic's own picture format
dtKm<n>                    a masked seg this frame: {seg}{x1}{x2}{s1}{s2}
                           {re}{rf} -- rebuilt by every BSP walk
dtVM<n>                    1 when visible-thing n is a masked seg, not a thing
dtLK<n>                    linedef special n -> the key it wants, 1 blue,
                           2 yellow, 3 red; ABSENT means none (\dtLineKey)
dtLQ<i>                    present when linedef i blocks sound
dtOt<i>                    what monster i is fighting; -1 is the player
dtHs<i>                    the sector thing i is standing in
dtSJ<s>                    sector s's two-sided lines, {line}{other} pairs
                           ending {-1}{-1}
dtSA<s> dtSQ/dtSR<n>       the noise flood's marks, and its queue
dtYR<y>                    per-row plane shade 0..13 (was dtRS -- see below)
dtSs<s>                    sector special; ABSENT means none (\dtSecSpec)
dtBT<s>                    the light thinker a special spawned: 0 none,
                           1 flash, 2 strobe, 3 glow, 4 fire.  Present for
                           every sector that HAD a special
dtBH<s> dtBL<s>            the two light levels it moves between
dtBC<s> dtBD<s>            frames to its next change; a strobe's dark time,
                           or a glow's direction
dtBX<k>                    light thinker k's sector
dtBW<k>                    scrolling wall k's sidedef
dtBG<k>                    seg k of a line that is fired by shooting it
dtEP<s> dtEQ<s>            moving floor: present if perpetual; the state a
                           stop line interrupted
dtHs<i>                    now recorded for EVERY thing wad.tex places
dtJh<t> dtJg<i>            missile type t homes; what missile i is following
                           (-1 the player, a thing index, -2 nothing)
dtCM32@<HH>                COLORMAP's inverse map, reached only via \dtFIXCM
dtCC<300+n>                answers n unclamped: how a fixed colormap gets
                           through the plane shader's clamp table
```

**`dtRS` was two arrays.** M16 gave a ceiling mover its state in `dtRS<s>`
and the procedural planes had kept their per-row shade in `dtRS<y>` since M1,
so marking a crusher on sector 3 repainted screen row 3 of every procedural
floor and ceiling, one shade wrong, for the rest of the run. Nothing
complained: a `\csname` array has no `\newcount` to refuse a second
declaration, and the rows near the top of a view shade to 0 anyway, which is
also what an idle mover's state is. It was found by reading the data model
above, where the same name appears twice. The shade is `dtYR` now. **Before
adding an array, search this table for its prefix.**

Missile states are `0` flying, `1` exploding, `2` spent -- the slot is free
and the next shot may have it. `2` is not `dtHG`: a thing that is taken is
gone for the rest of the run and `dtHG` says so permanently, while a missile
slot has to be handed back. Both are read in `\dt@thinggeom`, which is the
one place everything that looks at a thing goes through.

`dtJk` is the array with no counterpart in M15, and it is the whole reason a
missile cannot borrow `\dt@monframe`: a monster wears one sprite name through
all five of its states, and plasma flies as `PLSS` and explodes as `PLSE`.

The presence of `dtJs<i>` is "this thing is a missile", exactly as `dtOq<t>`
is "this type is a monster". Missiles carry no `dtOs`, so they are invisible
to the monster tick, to `\dt@mobjblock`, to the aim scan and to the touch
scan without any of them being told they exist -- which is also Doom, where
a missile is not `MF_SOLID` and not `MF_SHOOTABLE`.

Missiles live at thing indices `\dtMISBASE` and up, where `\dtMISBASE` is the
map's own thing count taken the first time something is thrown -- after
`\DTHOOK`, because several fixtures use the hook to drop a thing into the
built-in map. `\NTHING` grows to at most `\dtMISBASE + \dtMAXMIS`.

The register is `\dtMISBASE` and not `\dtNTH0` because **a control sequence
is letters only**: `\dtNTH0` parses as `\dtNTH` followed by the character `0`,
so `\newcount\dtNTH0 \dtNTH0=-1` declares one register, assigns `0` to it and
drops the `-1` on the floor. wad.tex records the same trap about `\dtU16`.

Ceiling states are `0` idle, `1` going down, `2` going up, `3` finished. A
crusher is the one that turns round at each end rather than stopping, which
is the whole of `dtRC`. Ceilings get their own arrays rather than borrowing
the door's because a door and a crusher can want the same sector, and Doom
lets them.

Monster states are `0` idle, `1` chasing, `2` attacking, `3` pain, `4` dying,
`5` dead. State 5 is never ticked and never aimed at, and it reads state 4's
letters -- a corpse *is* the death animation stopped on its last frame, so
`\dtMonAnim` declares `@5` as a copy of `@4` rather than giving it a table of
its own. Leaving it undeclared is not a missing picture but an expansion
accident: `\csname` binds the absent sequence to `\relax` and the frame
walker gets a control sequence where it wanted a letter. It cost 101 errors,
none of which named the table row that was short a line -- hence
`\dt@checkstates`, which says so in one line at load.

The presence of `dtOq<type>` is the whole of "is this a monster", exactly as
an absent `dtIK` is the whole of "not collectable".

**Both texel arrays are packed eight to a control sequence** and are read with
`\dt@pick` (textures and sky, where `u` is fixed per column so the pick is
settled once by `\dt@usetex`) or `\dt@fpick` (flats, where a plane needs 2D
random access and the pick index is a ninth argument). §6 says why this is not
optional. `dtX` is indexed by *group then row*; `dtFL` by *row then group*,
because a flat is indexed `[world y][world x]` and a texture column by `v`.

Rows `0..h-1` of a texture are the picture; **row `h` repeats row 0**, for the
same reason `dtT` has 65 rows: the span expansion reads `v` with `\numexpr`,
whose division rounds, so a `v` just short of the wrap asks for row `h`.

Door surfaces are chosen from `dtDT` at seg level, so they add no array of
their own; `\DOORRAMP` and `\TRAKRAMP` hold their palette bases.

Counts: `\NVERT \NLINE \NSEC \NSEG \NSSEC \NNODE \NTHING \NSIDE \NTEX
\dtNDOOR \dtNPLAT`, root node in `\DTROOT`. `\dtCMN` is how many `COLORMAP` light
levels are loaded; 0 means no WAD, and nothing on the texture path is ever
consulted.

Populate via `\dtAddVert \dtAddLine \dtAddLineSpec \dtAddLineTag
\dtAddLineSide \dtAddSector \dtAddSectorTag \dtAddSeg \dtAddSegOff
\dtAddSSec \dtAddNode \dtAddThing \dtAddSide`. **Use
these rather than writing csnames directly** -- `\dtAddSeg` also computes the
seg length via `\dtIsqrt`, which both texture mapping and collision need.
`\dtAddLineSide` and `\dtAddSegOff` are separate from `\dtAddLine`/`\dtAddSeg`
rather than extra arguments to them, so that the built-in map's existing calls
keep working and stay on the procedural tables.

`\dtANIM` is the animation clock, advanced once per rendered frame by
`\dtAnimTick`.  It is a plain frame count, so a sequence declared with Doom's
own tic numbers runs at those numbers measured in frames.

### Player state

Everything below the position is new in M14, and all of it is `\newcount`:

```
\dtHEALTH \dtARMOR \dtARMCL     health, armour points, armour class
\dtWEAP \dtBACKP                readyweapon, and whether a backpack was found
\dtNPICK                        how many things have been taken
\dtGOT                          did the touch just now actually give anything
\dtPICKR                        the box test's half-width, 36 (16 + 20)
```

```
\PX \PY \PZ    position and eye height, 16.16 fixed point
\PA            facing, 8192 units to a full turn
\dtPSX \dtPSY  current query point in MAP UNITS (not 16.16)
\dt§EC        subsector containing it
\dtSECN        sector containing it
\VIEWH         eye height above the floor, 41
\PRAD \PSTEP \PHEAD   player radius 16, max step up 24, min headroom 56
```

`\dtPSX`/`\dtPSY` are the one thing to be careful with: `\dtPointSide` reads
them rather than `\PX`/`\PY`, so **anything that calls `\dtRenderBSP` or
`\dtUse` must set them first**. `\dtViewSetup` does, and every entry point
goes through it.

### Count register banks

`\newcount` allocations sit low (about 130 of them now, against plain TeX's
limit of 255). Direct banks:

```
1000 + x    \CT{x}   first open row in column x
2000 + x    \CB{x}   one past the last open row
3000 + x    \CD{x}   scale at which a solid wall closed the column
4000 + x    \PU{x}   world direction of column x's ray, x component, *4096
5000 + x    \PV{x}   ditto, y component
6000 + x    \PK{x}   sky texture group for column x
7000 + x    \PJ{x}   which of the eight texels in that group
8000 + cy   \dtASB{cy}  viewport row cy's luma accumulator (play.tex)
```

`\CD` is inverse depth, so larger is nearer and 0 means nothing closed the
column. `\PU`/`\PV`/`\PK`/`\PJ` are rebuilt once a frame by
`\dtPlaneFrame`, because all four depend on the facing. eTeX gives 32768
registers, so **new per-column arrays should start at 9000, 10000, ...**.

`\dtASB` is the odd one: it is indexed by viewport **row**, not by column,
and needs at most `\SCRH/2` entries. A column walk touches a whole column of
cells at once, so a column of accumulators is what has to be live, and it is
flushed into the row macros every `n` framebuffer columns.

The 1000 spacing is what bounds the framebuffer, and it always has: `\PJ{x}`
reaches `7000+\SCRW`, so a render wider than 1000 columns walks into
`\dtASB`, and `\dtASB` itself reaches `8000+\SCRH/2`. Neither is a
configuration anybody wants -- 1000 columns is five times Doom's own -- but
if one ever is, respace the banks rather than squeezing a new one in.

**Every named register now comes from `\newcount`. Never pick a register
number by hand.** There used to be a block of `\countdef`s at 300..399, on the
premise that plain TeX's `\newcount` stops at 255. Both halves of that were
wrong. TeX Live builds the `pdftex` format from `etex.src`, so `\newcount` runs
well past 255 here (300 allocations reach 350 with no error) -- and under
**LaTeX** the allocator starts around 250 and runs straight through 300..399,
so `\VIEWH` and `\dtHDA` became the *same* `\count352` and the HUD pass
overwrote the view height on every frame. See §6.

The direct banks above are still safe: LaTeX's allocator reaches 458 with this
file loaded and no packages, so 1000 and up is clear by a wide margin. New
per-column arrays still start at 4000, 5000, ...

### Configuration macros

Define before `\input doom.tex`:

```
\DTW \DTH \DTPIX \DTFRAMES      framebuffer, scale, frame count
\DTWAD \DTMAP                   load a real WAD
\DTNOTEX                        keep every surface on the procedural tables
\DTNOFLAT                       keep only the planes procedural
\DTSKY                          sky texture name (default SKY1)
\DTX \DTY \DTANG                override start position and facing
\DTTURN                         rotate this much after every step
\DTUSE                          press "use" every frame
\DTFIRE                         hold the trigger down every frame
\DTNOMON                        no monsters: nothing ticks and nothing is solid
\DTNOMIS                        no projectiles: nothing is thrown, and no
                                missile picture is read
\DTNOMASK                       every middle texture stays opaque
\DTNOLOCK                       locked doors open for anybody
\DTNOSND                        a gunshot wakes nothing; sight alone again
\DTNOWAIT                       no weapon cooldown: one shot a frame
\DTNOZ                          sight ignores height, as it did before M21
\DTPLAY                         interactive mode
\DTASC                          n: a terminal cell is n x 2n render pixels
\DTPIPE                         hand the frame to a front-end, do not draw it
\DTNOSPEC                       sector specials are inert: no light thinkers,
                                no burning floors, no scrollers, no secrets
\DTNOPOW                        the timed powers stay on the floor
\DTNOAIM                        a player's missile flies level
\DTNOHOME                       a revenant's missile flies straight
\DTHOOK                         run once, after load, before the first frame
```

`\DTW`/`\DTH` are the **render** resolution and therefore the PDF's, in
interactive mode exactly as in batch. `\DTASC{n}` shrinks only what the
terminal is shown: the viewport is `\DTW/n` by `\DTH/2n` cells, each the mean
of the block behind it. The default is 1, which is a 1x2 mean and the same
shape this file has always printed -- the row it used to throw away now
counts toward the cell instead. A trailing partial block is dropped rather
than averaged over a short divisor.

**`\DTUSE` and `\DTFIRE` are read BEFORE `\DTHOOK` runs**, because
`\dtUseStep` and `\dtFireStep` are settled from them at that point. Setting
either inside `\DTHOOK` compiles, changes nothing, and reports a player who
pulled the trigger forty times without spending a bullet. Put them in the
fixture's preamble.

`\DTNOMON` spawns nothing at all rather than spawning and freezing, and the
difference matters: a thing with no `dtOs` goes back through the per-type
spawn loop in `\dt@thingkey`, which is the animation every check written
before M15 was watching. Spawning and freezing would have frozen those too.

`\DTHOOK` is the only way a caller can execute anything: `doom.tex` ends with
`\end`, so everything after `\input doom.tex` is dead code. Several regression
fixtures use it to install a door or drop a thing into the built-in map.

---

## 5. Pipelines

### Render

```
\dtRenderFrame
  \dtRenderScene
      \dtViewSetup                 normalise \PA, sin/cos, \dtPSX/\dtPSY, clear clip
      \dtRenderBSP{\DTROOT}
          \dtPointSide{node}       cross product -> \dtSIDE
          recurse near child, then far child, while \dtOPEN > 0
          \dtSubsector{s}          loop segs dtCf..dtCf+dtCn
              \dtSegDraw{seg}      transform, near-clip, project to x1/x2
                  \dt@column       per-column scale, texture u, shade
                      \dt@solidcol    ceiling + wall + floor, close column, set \CD
                      \dt@portalcol   ceiling + upper/lower + floor, narrow
                                      CT/CB; if the opening collapsed, close
                                      the column and set \CD like a wall
      \dtBuildColumns              dtTS + void filler + dtBS -> dtFC<x>
      \dtDrawThings                project, sort far to near, splice
          \dt@maskvis              M19's drawsegs join the same list
          \dt@maskdraw             ...and are drawn by \dt@postwalk
          \dt@thinggeom            view-space transform, screen box
          \dt@thingcols            per column, test scale against \CD{x}
              \dt@thingband        \dtSpliceCol into dtFC<x>
  \dtEmitColumn{x} -> \dtColumn -> \pdfliteral page inline image
  \shipout
```

### The viewport, and the pipe (play.tex)

```
\dtKeyLoop
    \dtAsciiTable              once: dtLV<HH>, palette byte -> luma 0..255
    \dtAsciiSetup              once: \dtASN, \dtASV=2n, \dtVPW, \dtVPH, \dtASA
    per frame, after \dtRenderScene and BEFORE the \shipout:
        \dtAsciiFrame          ...or \dtPipeFrame under \DTPIPE
            \dt@tr / \dt@trN   walk a column two hex digits at a time,
                               adding each pixel's luma to the cell it
                               falls in -- a transpose and a box filter in
                               one pass
            \dt@trF            every n columns: accumulator -> character,
                               appended to dtRow<cy>, and cleared
        \dtPipeFrame
            \dtPipeState       [DTS] ... one long line, wrapped
            [DTF] w h / the dtFC<x> columns verbatim / [DTE]
```

Three things about this are load bearing.

**The filter reads the assembled columns, and writes nothing back.** `dtFC<x>`
is the same macro `\dtEmitColumn` hands to the PDF, so the decimation cannot
reach the render even by accident -- which is the whole point of `\DTASC` and
is what `check.py` asserts by rendering the same walk at `\DTASC{1}` and
`\DTASC{2}` and comparing the two PDFs byte for byte.

**The pipe block is emitted before the `\shipout`.** pdfTeX prints its own
page marker on the same stream, and putting the block first keeps `[1]`
between blocks rather than through the middle of one.

**It cannot deadlock on a full pipe buffer.** TeX flushes the terminal
immediately before every blocking read (`term_input` calls
`update_terminal`), so by the time it is waiting for a key everything it
wrote has left. The prompt it writes -- `\dt@key=`, with no newline -- is
what the *next* line arrives glued to, so every marker is searched for
anywhere in a line rather than at its start. `check.py`'s `asciirows()` has
to strip the same prefix, and did not at first: it lost the first row of
every frame but the first, quietly, and reported 29 rows where there were 30.

### Movement

```
\dtStepFwd{dist}                   the ONLY movement entry point
  \dtTryMove{newx}{newy}           P_TryMove
      \dtMoveTest{ox}{oy}{nx}{ny}  full move; then x-only, then y-only
          \dtBlocked
              \dt@segblock         swept bounding-box reject
                  \dt@segcross     does the move cross this seg?
                  \dt@segnear      is the destination within the radius?
                  \dt@segopen      P_LineOpening: headroom and step height
      \dtLocatePlayer              -> \dt§EC, \dtSECN
      \dtFollowFloor               ease \PZ toward floor + \VIEWH
```

### Doors, lifts and triggers

```
\dtFindDoors                       once, after the map loads
    \dtLineAction{special}         six fields out of one number
    \dt@markline
        back sector, or \dt@fortag over every sector with the line's tag
        \dt@markdoor               record shut/open heights per door sector
            \dt@doorhigh           lowest neighbouring ceiling less four
        \dt@markplat               record low/high targets per floor sector
            \dt@floorlow/high      lowest / highest neighbouring floor

\dtUse                             P_UseLines: nearest usable LINE within 64
    \dt@usenear                    in front, by dot product with the facing
    \dtFireLine{line}              the one entry point for every route
        \dt@fireact
            \dt@firedoor -> \dt@usedoor (by hand) or \dt@doortrig (trigger)
            \dt@fireplat
            \dt@swline             SW1 <-> SW2 on the front sidedef
\dtCrossLines                      from \dtTryMove, on the move that was taken
    \dt@crossseg -> \dt@segcross   collision's own crossing test
        \dt@crossfire              walk-activated lines only

\dtSectorTick                      once per frame
    \dtDoorTick                    animate dtSc
    \dtPlatTick                    animate dtSf, and \dt@platride the camera
    \dtCeilTick                    animate dtSc again, and \dt@crushride
```

`\dt@markline` and `\dt@fireact` both dispatch on `\dtTKIND` through one
`\ifcase`, so a new kind is a branch in each and a row in the table. Kinds 4
(stairs), 5 (lights) and 6 (the exit) mark nothing at load: a staircase's
targets are a chain settled when its trigger fires, and a light and an exit
have no state to keep. The exit is taken *before* the tag scan, because it
acts on no sector and its tag field is 0 -- which `\dt@fortag` matches to
nothing by design.

`\dtDoorTick` still exists and still means what it did; the door fixtures
drive it directly. `\dtSectorTick` is what the frame loop and the key loop
call.

### Pickups and the bar

```
\dtTryMove                         after the move is committed
    \dtTouchThings                 P_CheckPosition's item half
        \dt@touch                  |dx| and |dy| against \dtPICKR, then height
            \dt@give               kind -> one of eleven give routines
                \dtGiveBody \dtGiveArmor \dtGiveAmmo \dtGiveWeapon \dtGiveKey
                mark dtHG<i> and count it, IF \dtGOT came back set

\dtDrawHUD
    \dtBlit{STBAR}                 the bar, anchored to the bottom
    \dt@hudstate
        \dt@hudnum{value}{right edge}{digits}
            \dt@huddig             one digit, walking leftward
                \dt@hudpat         V_DrawPatch: subtract the patch's offsets
        \dt@hudface
            \dt@facepick           pain level and glance -> \dtFaceKey
    \dtBlit{\dtWeapon}             dtUS<\dtWEAP>, so it follows the weapon
```

`\dtTouchThings` goes **after** `\dtLocatePlayer`, because it reads
`\dtPSX`/`\dtPSY` and the floor under the player and that call is what settles
all three. It is the same rule `\dtRenderBSP` and `\dtUse` follow.

### Enemies

```
\dtSpawnMonsters                   once, after the map loads
    \dt@checkstates                every state a spawned type can reach exists

\dtThingTick                       once per frame, guarded by \ifdt@mon
    \dtPLX/\dtPLY := the player    ONCE, at the top -- see below
    per thing carrying dtOs:
        \dt@monadv                 one tic off the frame; \dtMBG, \dtMBW
        \dt@monidle                A_Look, only on a frame change
            \dtSight               -> \dtSEE
        \dt@monchase               A_Chase
            \dt@mondist            P_AproxDistance
            \dt@monshoot           in range, clear line, and a die roll
            \dt@monmove            P_Move -> \dtMoveTest with dtMRAD/dtMHGT
                \dt@monstep        commit, and re-locate onto its new floor
        \dt@monatk                 fire on the last letter, then back to chase
            \dt@monmelee / \dt@monbullets -> \dtDamagePlayer
        \dt@monhurt \dt@mondie
    \dtLocatePlayer                put \dtPSX/\dtPSY/\dtSECN back

\dtFire                            one press of the trigger
    \dt@fireweap                   pellets, and is there ammunition
        \dt@firegun                spend it, settle \dtPLX/\dtPLY and the facing
            \dt@aim                nearest live monster the ray passes inside
                \dtSight           one scan, to the winner only
            \dt@hitscan            per pellet, then \dtHurtThing

\dtPlayerTick                      the face's three timers, P_DeathThink,
                                   and the weapon coming round
```

`\dtFire` is now gated on `\dtWCOOL`, and `\dt@hitscan` aims **per pellet**:

```
\dtFire                            one frame with the trigger held
    \dtWCOOL                       refused while the weapon is still busy
    \dt@fireweap                   pellets or a missile, and the ammunition
        \dt@firegun / \dt@firemis  spend it, and \dtNoiseAlert
            \dt@hitscan
                \dt@pellet   x N   Doom's spread, on the loop counter \dtSHL
                    \dt@aim        ...which is why it cannot be \dtSHN
                    \dt@pellethit
```

### Sound

```
\dtBuildSound                      once, after the map loads
    dtSJ<s>                        every two-sided line touching sector s

\dtNoiseAlert                      P_NoiseAlert, from \dt@firegun
    \dt@sndgo                      clear dtSA, locate the player, flood
        \dt@sndpop -> \dt@sndwalk -> \dt@sndline
            P_LineOpening, then at most one ML_SOUNDBLOCK
        \dt@sndwake                every idle monster in a marked sector
    \dtLocatePlayer                put \dtPSX/\dtPSY/\dtSECN back
```

### Projectiles

```
\dtSpawnMissile{type}{sx}{sy}{sz}{tx}{ty}{tz}{owner}
    \dt@misslot                a spent slot, or one more thing
    \dt@misspawn               bearing, velocity, momz, and clear of the shooter
  reached from \dt@monmissile (A_TroopAttack's other arm)
              and \dt@firemis  (P_SpawnPlayerMissile)

\dtMissileTick                     once per frame, after \dtThingTick
    \dtPLX/\dtPLY := the player    ONCE, at the top -- \dt@monall's rule
    per thing carrying dtJs:
        \dt@misfly             state 0
            \dt@misadv         one tic off the frame
            \dt@misstep        x N, each at most \dtMISSTEP units
                \dtMoveTest    with \ifdt@mis, so \dt@segopen tests z
                \dt@mishitpl   the player, who is not in \dt@mobjblock
            \dt@missector      P_ZMovement: the floor and ceiling it is in
            \dt@misboom        P_ExplodeMissile
                \dtHurtThing / \dtDamagePlayer
                \dt@misblast   P_RadiusAttack
                \dt@misspray   A_BFGSpray
        \dt@misdying           state 1; the slot goes back at the wrap
    \dtLocatePlayer            put \dtPSX/\dtPSY/\dtSECN back
```

`\dtMissileTick` runs **after** `\dtThingTick`, and both halves matter. A
missile thrown this frame must not also move this frame, which is what Doom's
thinker list gives anything spawned during a tick; and `\dt@misboom` borrows
`\dtMBI` to reach `\dtHurtThing`, which is the register the monster tick
loops on. It carries a `\dtLocatePlayer` of its own for `\dt@monall`'s
reason: putting a missile on its new sector runs the only point locator here.

Three registers are load bearing across the tick and the names are worth
keeping straight: `\dtJI` is the missile, `\dtJK` the loop that found it, and
`\dtJJ` every scan an explosion runs from inside that loop -- an explosion
walks the thing list while the tick is walking the thing list.

**`\dtPLX`/`\dtPLY` are not `\dtPSX`/`\dtPSY`, and that is load bearing.**
A monster that moves has to be put on the floor of the sector it moved into,
and `\dtPointInSSec` is the only point locator here -- it writes
`\dtPSX`, `\dtPSY`, `\dtSSEC` and `\dtSECN`, which are the registers the
*player's* position lives in. Aiming the tick at `\dtPSX` would send every
monster after the first toward wherever the previous one ended up. Worse,
`\dtTryMove` reads `\dtFloorZ` -- that is `dtSf<\dtSECN>` -- on its first
line, so a stale `\dtSECN` measures the player's next step up against a floor
on the other side of the map. `\dt@monall` takes a copy at the top and calls
`\dtLocatePlayer` at the end; one BSP descent buys the whole hazard away.

`\dtMoveTest` is shared with the player, so the mover's size travels in
`\dtMRAD`/`\dtMHGT` and its identity in `\dtMSELF`. `\dtTryMove` resets all
three to the player's on every call, which is what keeps the player's route
through the scan exactly the route it was -- verified pixel-identical on the
built-in map before anything else went in.

`\dtUse` sets `\dtPSX`/`\dtPSY` and `\vsin`/`\vcos` itself rather than
trusting the caller to have run `\dtViewSetup`, because it is reachable from
`\DTHOOK` and from the key loop as well as from the frame loop. Four
assignments a press to remove an ordering hazard.

### Sector specials, teleporters and rising floors (M23)

```
\dtSpawnSpecials                   once, after the map loads; guarded on dtBT
    \dt@specone                    count a secret; pick a thinker kind
        \dt@litespawn              levels from \dt@lightnear, start count

\dtSectorTick
    \dtSpecTick                    under \ifdt@spec; advances \dtLEVELT
        \dtLightTick               \dt@liteflash / strobe / glow / fire
        \dtScrollTick              dtYx of each listed sidedef += 2 or 3
        \dtFloorTick               P_PlayerInSpecialSector
            \dt@floorspec          a secret, or \dt@slime -> \dtDamagePlayer

\dtFindDoors
    \dtFindLists                   rebuilt from nothing every call:
                                   dtBW (scrollers), dtBG (shot lines)

\dtCrossLines                      \dtTPDONE=0
    \dt@crosstest                  \dtXSIDE from \dtDA and the seg's side
        \dt@crossfire -> \dtFireLine -> \dt@fireact -> \dt@fortag
            \dt@fireteleport       needs FIRED=0, XSIDE=0, TPDONE=0
                \dt@teledest       first type-14 thing in the tagged sector
                    \PX \PY \PZ \PA written; \dt@telefrag
            \dt@fireraise          only a floor standing still
                \dt@raisenext / \dt@raiseceil / \dt@raisetex
                    \dt@nbrwalk    one walk; \dt@nbrdo is \let per question
                \dt@raisego        dtES=3 with dtEH the target (a stair)

\dt@pellet                         after the aim scan and the hit
    \dt@shootlines                 only if \dtNGUNL>0
        \dt@shootseg               crossing along the ray, nearer than the
                                   monster the aim found
        \dtSight                   to four units short of it
        \dtFireLine
```

`\dt@fireteleport` is reached from inside `\dtTryMove`, after the move was
committed and before `\dtLocatePlayer`, the touch scan and `\dtFollowFloor`
-- so writing `\PX`/`\PY` there simply replaces where the move ended and
everything `\dtTryMove` was about to do happens at the far end unasked.

**`\dtTPDONE` is not decoration.** The crossing scan meets every seg of a
linedef, measured from where the move began, so a repeatable teleporter the
BSP had cut in two fired twice in one stride. The doors' answer to the same
problem -- act only on a sector that is standing idle -- has nothing to hold
on to when what is being moved is the player.

A floor's rising target is settled when the line FIRES, where a lowering
floor's is settled at load (§5 above, and the note on `\dt@markplat`):
"the next floor up from here" is a question about where the floor has got
to. The cost is that a sector which is both somebody's lift and somebody's
rising floor comes back from a raise with the lift's own targets overwritten.

### Powers and the fixed colormap (M23)

```
\dt@give -> \dt@gpower             kind 12: one of four timers
\dtPlayerTick                      run each timer down, then
    \dt@powerview                  visor -> 1, invulnerability -> 32, each
        \dt@blink                  blinking through its last 51 frames
        \dt@setfixcm               \dtFIXCM; saves and zeroes \dt@plit

\dtFIXCM read at:   \dt@column      \dtWCM, per wall column
                    \dtPlaneSeg     \dtPSM = 172 + n, per seg
                    \dt@sprcols     \dtSRM, per thing
                    \dt@maskshade   \dtKD, per masked column
                    \dtDrawHUD      \dtBLM, for the weapon only
```

No pixel pays for it. A plane pixel's level is `dtCC<psm - plit/(D+256) +
128>`, a clamp table; with `\dt@plit` zeroed and `psm` set to 172 + n the key
is 300 + n, and `dtCC<300+n>` answers n. That is the only way a table past
31 can be reached at all, which is deliberate: table 32 is not a light level
and no arithmetic on a light should be able to arrive at it.

`\dt@setfixcm` saves `\dt@plit` rather than assuming 20480, because a
fixture is allowed to have moved it (§1, "planes shade with distance").

### Texture load

```
\dtLoadWAD
    SIDEDEFS loop
        \dtTexRef{offset}          read a name, uppercase it, want it
            \dtWantTex             allocate an index; width 0 until resolved
    \dtLoadColormap                before the textures, and before the things
    \dtLoadTextures
        \dt@loadpnames             PNAMES  -> dtXp<i>
        \dt@scantex{TEXTURE1}      resolve wanted names -> size, record offset
        \dt@scantex{TEXTURE2}
        \dt@wantpatches            mark every patch a wanted texture names
        \dt@findpatches            ONE directory pass -> dtXl<namehex>
        per texture: \dt@compose
            \dt@drawpatch          per patch, per column
                \dt@txposts        post chain
                    \dt@txrunF/G   pixels -> dtX<t>@<u>@<v>
            \dt@zclear             scratch to palette 0, so a hole is a pixel
            \dt@zcolumn            every patch covering this column, in order
            \dt@zpack              eight columns -> one control sequence a row
            row h := row 0         to absorb \numexpr's rounding
            \dt@maskread           only for a middle on a two-sided line:
                \dt@readpic        one patch at (0,0) -- its own posts
                \dt@maskbuild      anything else: \dt@zcolumn again, into a
                                   column cleared to a MARKER, then runs of
                                   drawn rows gathered back into posts
```

Reading a lump per patch would mean a directory scan per patch, which is why
this collects first and resolves in one pass -- exactly the shape
`\dtLoadSprites` uses, and for the same reason: `\dtFindLump` scans from a
given entry and this WAD has 2928 of them.

### The one design idea worth internalising

Rendering front to back means a column fills **from the outside in**: the top
half only ever grows downward, the bottom half only ever grows upward. So a
column is two accumulators -- append to `dtTS`, prepend to `dtBS` -- and
nothing ever needs random access into the middle of a column *during* the wall
pass. At assembly:

```
column = dtTS<x>  +  void filler (CB-CT bytes)  +  dtBS<x>
```

The lengths are guaranteed to sum to `\DTH`. This is why the column-length
invariant is such a good check, and why sprites need `\dtSplice` -- they are
the one thing that *does* write into the middle, after assembly.

### Coordinate conventions

- Fixed point 16.16 for camera position and view-space arithmetic. Map arrays
  store plain integers, and **collision works in map units**, which is what
  keeps its products inside 32 bits.
- Angles: 8192 units to a full turn (Doom `FINEANGLES`), `\PA`.
- Depth is `+x` in view space; lateral is to the viewer's **left**, so
  `x = SCRW/2 - l*PPLANE/d`.
- `\PPLANE = SCRW/2` gives a 90 degree horizontal FOV.
- **Scale is `PPLANE*65536/depth_map`.** A 16.16 height difference becomes
  pixels with *two* divisions by 65536; a plain map length needs *one*. Mixing
  these up is a silent factor of 65536 (§6).

---

## 6. TeX traps

Most of these fail **silently**. They cost roughly a day each.

**Read this section before writing code in this project, not after.** Two of
these caught me within an hour of writing them down: the `\tmpa` aliasing one
and the digits one, both below. Adding real flats hit four more, three of
which are in this section already and one of which -- the `\dt@pk` collision
-- is this section's own advice ignored.

**`max_strings` is the binding resource, not time and not memory.** pdfTeX
allows 496,994 strings under `pdftex` and 476,106 under `pdflatex` (the
format takes the difference), and every `\csname` costs one. One control
sequence per texel put MAP01's 39 wall textures at **419,768 strings, 92% of
the pool**, and the first flat killed the run with `TeX capacity exceeded
[number of strings]` partway through the second flat -- with no other
symptom, because nothing else is short.

The answer, everywhere in this project now, is to **pack eight texels to a
control sequence** as eight braced pairs and pick one with an
eight-parameter macro. Textures pack along `u`, which is fixed for a column
span, so the group and the pick are settled once per column by `\dt@usetex`
and the inner loop pays a macro call and no arithmetic. Flats pack along `u`
too but a plane needs 2D random access, so they pay a `\ifcase`. This cut
the pool from 477k to **111k**, and it is *faster*: 0.84x on the geometry
pass, because 111k strings hash better than 477k.

Two rules follow. Anything you add that wants a per-texel array must pack it.
And **scratch that is redefined rather than allocated costs nothing** --
re-`\gdef`ing a control sequence makes no new string, which is what lets the
column-major compositor hold eight scratch columns and reuse them for the
whole texture set.

**`\csname X\endcsname` expands to the control sequence, not to its body,
and macro arguments are not expanded while being grabbed.** So feeding a
packed group to a picker needs *three* `\expandafter`, not one:

```tex
\expandafter\expandafter\expandafter\dt@pick\csname\dt@ux ...\endcsname
```

With one, the picker takes the control sequence itself as its first texel
and eats `\endcsname` and everything after it hunting for the other seven.
The error it eventually raises names a macro several expansions away from
the mistake.

**A space after a CHARACTER inside `\csname` is part of the key.** Spaces
after `\relax`, after `\the\count` and after a digit sequence are absorbed
by the number scan and are safe; a space after `@` is not:

```tex
... \endcsname @\expandafter\dt@fpick ...   % key becomes "dtCM12@ 4F"
```

`\csname` quietly binds an undefined name to `\relax`, so this does not
error where it happens -- the `\relax` travels into the column and detonates
somewhere else. The asymmetry is worth confirming in a three-line test
rather than reasoning about; it took one to settle.

**A line ending after a control word produces no space token.** TeX is in
"skipping blanks" state, so this does not terminate the number scan:

```tex
\rb=\ifnum\x@two>\SCRW \the\SCRW\else\the\x@two\fi
\dtWhile{\cx<\rb}{...}
```

The scan ran onto the next line and **expanded `\dtWhile` itself**, executing
the column loop from inside the assignment. It drew 16 columns and stopped,
leaving 59% of the screen void. Always terminate with `\relax`.

**Instrumentation hid that bug.** Adding any register assignment before the
loop gave the runaway scan something unexpandable to stop at, so the fault
vanished whenever it was measured. *If a debug build behaves differently from a
plain one, diff the two files and bisect the difference.*

**A macro grabs its arguments before a later `\expandafter` fires.**
`\expandafter\dtColumn\expandafter{\the\cx}\expandafter{\dt@col}` passes
`\expandafter` as the second argument; the data then gets *typeset* and every
inline image comes out empty. When two arguments need pre-expansion, do them
one at a time. **`\dtSpliceCol` shows the other side of this**: it genuinely
needs the single `\expandafter` idiom, because `\dt@spN` grabs two character
tokens and macro arguments are *not* expanded while being grabbed -- an
unexpanded `\csname` would be swallowed as pixel data.

**`\fi`, `\else` and `\or` are EXPANDABLE, so a number scan runs straight
through them and takes the conditional with it.** This is the line-ending trap
again, but the runaway has somewhere much worse to go. A branch ending in a
bare assignment:

```tex
\def\dt@gbonus{... \dtGOT=1}          % called from \ifcase ... \or\dt@gbonus
```

leaves `\dtGOT=1` still scanning for further digits when the `\or` arrives.
`\or` is expandable, so the scan *expands* it -- which closes the `\ifcase` --
runs on to the `\ifnum\dtGOT=1` that was meant to read the result, expands
**that** too, and evaluates it while the assignment making it true is still in
flight. The flag reads 0, the branch is skipped, and the assignment then
completes: a health bonus that raised health and was never collected, zero
errors, plausible frame.

Two things make this worth its own entry. A minimal reproduction **passed**,
because the token after the branch there happened to be an unexpandable
`\message`; the fault only appears when what follows is itself expandable. And
`\meaning` of the macro was perfectly correct, so reading the definition told
me nothing. **Terminate every assignment that ends a branch with `\relax`.**

**`\numexpr` division rounds, it does not truncate.** All modular arithmetic
must go through `\divide`. It also bites in `\dtFollowFloor`: a gap small
enough that `gap/2` rounds to zero never closes, so the snap threshold must be
on the gap, not the step.

**Digits are not letters.** `\dtU32` parses as `\dtU` followed by the
characters 32, so `\dtU16` and `\dtU32` silently define the *same macro* with
conflicting parameter texts. I re-learned this by declaring `\newcount\dtTX1`
and `\newcount\dtTX2`, which produced 101 errors about `\numexpr` in
horizontal mode. Macro names: letters only. `\csname` names may use digits.

**Register aliasing.** `\tmpj` was once both the frame counter and a seg's
vertex index, so the frame loop never advanced. I then repeated the mistake:
`\dtPointSide` writes `\tmpa`, and I used `\tmpa` as the bound of the loop that
calls it through `\dtPointIn§ec`, so the thing loader ran zero iterations and
cheerfully reported "0 things placed". **Do not use `\tmpa`-`\tmpk` across a
call into anything.** Collision, things and doors each have their own register
sets for this reason.

**A hand-picked register number is a collision waiting for a different
format.** The block of `\countdef`s at 300..399 was clear under plain TeX,
where the allocator stops around 200. Under LaTeX it starts around 250, so
`\VIEWH` *was* `\dtHDA` and every frame the HUD drew reset the view height to
40960. The player walked the right path with the camera sinking through the
floor -- no error, no failing check, because everything in `check.py` ran under
`pdftex` alone. **A cross-format check is the only thing that sees this**, and
there is one now. Use `\newcount`.

**Register aliasing has a twin in the macro namespace, and it is worse.**
`\dt@thingrot` wrote `\dt@tr`; so did play.tex, for its transpose walker,
once at load.  Per-frame scratch clobbered a definition that had to survive a
frame, and the symptom was 100 Overfull hboxes a frame in interactive mode with
no error and a correct-looking picture.  Registers at least announce
themselves; a macro just quietly becomes something else.  **Before naming a
`\dt@xx` scratch, grep the other two files for it.**  Engine scratch and
client state must not share a namespace this small.

It happened a second time, in the same slot, to someone who had read the
paragraph above.  The flat texel picker was named `\dt@pk`; play.tex holds
the pressed key in `\dt@pk`, having been renamed there from `\dt@k` for
exactly this reason.  Pressing `w` redefined the picker to the letter `w`,
and the braces of the group it should have taken apart travelled into the
column for `\dt@spDN` to trip over.  Batch mode never sees it -- only the
interactive IWAD fixture did.  **Two of the three files reserve `\dt@` names
for client state; grep is not optional and reading this section is not a
substitute for running it.**

**A stub in a fixture does nothing unless `@` is a letter there.**
`\def\dt@onepost#1#2#3{}` with `@` at catcode 12 defines `\dt` with the
parameter text `@onepost#1#2#3` -- it compiles, it changes nothing, and the
bisection row reads "this code is not involved".  Set `\catcode`\@=11` in the
fixture.  This produced a bisection table in §8 that contradicted itself for
months.

**Print `\meaning` at the point of use.** Every expansion theory about the
leak above was wrong; `\meaning\dt@tr` said `macro:->8` and ended it.

**A macro that assigns into an argument is not a macro that returns a value.**
`\dtIsqrt#1#2` sets `#1`. Written `\dtDK=\dtIsqrt{...}`, the register
assignment consumes the `\isq@n` at the head of the macro body as its number,
the assignment ends, and the rest of the body is left as ordinary text -- so a
bare `=` starts horizontal mode and the error names whatever primitive follows.
It reported itself as `You can't use \numexpr in horizontal mode`, a message
naming neither `\dtIsqrt` nor anything actually wrong.

**An included file can change catcodes under you.** `wad.tex` and `play.tex`
both end `\catcode`\@=12`, so everything below `\input wad.tex` parses
`\dt@foo` as `\dt` followed by `@foo`. It breaks *only when a WAD is loaded*,
so the built-in map compiles clean and the fault reads like a WAD bug.
`doom.tex` now restores the letter catcode after the input; keep that line.

**An error prompt eats a line of stdin.** Interactive mode needs TeX's normal
interaction mode, in which an error asks the terminal what to do -- and a
fixture feeding keystrokes answers it with the next key. If that key is `q`,
TeX enters batchmode and every later error goes to the log alone. `check.py`
counted stdout, and so reported `interactive: compiles clean, 0 errors` while
the log held sixteen. **Count errors from the `.log`, never from stdout.**

**`\numexpr` takes a leading minus only before a number.** `\numexpr-(a-b)`
is an error; `\numexpr0-(a-b)` is not. The error names the enclosing
assignment.

**`\ifnum` pushes back the token that ended its operand.** Writing
`\ifnum\dtSRC>\dt@srH\relax \dt@srH\else...\fi` looks like the fix for the
trap below, and the `\relax` does stop the number scan -- but `\ifnum` puts it
back, so it arrives at the *head of the branch* and the branch value is never
read. The error names the assignment, not the conditional. A count register is
a complete internal integer and needs no terminator, which is why the resampler
keeps `\dtSRS`, `\dtSRT` and the clip bounds in registers rather than macros.

**A space after a control word is not a token either.** Same cause as the
line-ending trap above. `\ifnum#2>\dt@srH \dt@srH\else#2\fi` reads *both*
macros as a single number -- `73` then `73` becomes `7373` -- and the branch
disappears into it. This one cost an hour on the first sprite build.

**`\csname` binding an absent key to `\relax` is a trap wherever the result
is DATA rather than a name.** The texel arrays get away with it -- an
undefined texel raises `Missing \endcsname` at the point of use. A frame
sequence does not: `\csname dtOa3004@5\endcsname` for a state nobody declared
expands to `\relax`, the walker takes it as its first letter, and the run
ends with a hundred errors naming `\dt@anA` and nothing naming the table row
that was short a line. **If an absent entry would be read as data, check for
it at load and say so once.** `\dt@checkstates` is that check.

**A configuration macro read before `\DTHOOK` cannot be set inside it.**
`\dtUseStep` and `\dtFireStep` are settled from `\DTUSE` and `\DTFIRE` above
the hook, so a fixture that defines either *in* the hook compiles, changes
nothing, and reports a player who pulled the trigger forty times without
spending a bullet. The general shape is §11's "a stub that changes nothing
has two explanations".

**Two `\newcount`s of the same name are one register, and this project had
six such pairs.** `\dtAX`/`\dtAY` were declared for `\dtAtan` and declared
again with the collision scratch, so the arctangent and `\dt@segblock` wrote
the same two registers. Nothing did both at once until monsters moved, and
from then on every caller of `\dtAtan` had to take its bearing and copy
`\dtANG` out *before* going anywhere near `\dtMoveTest`. Four more were split
between files -- `\dtXL`, `\dtXH`, `\dtNDP` and `\dtSTG` each meant one thing
in `doom.tex` and another in `wad.tex` -- and were safe only because one
side ran at load and the other per frame. M23 gave every one its own
register (`\dtATX`/`\dtATY`, `\dtBXL`..`\dtBYH`, `\dtNODP`, `\dtSCG`), and
this one-liner now prints nothing:

```
grep -oh 'newcount.[A-Za-z@]*' doom.tex wad.tex play.tex | sort | uniq -d
```

Run it before `\newcount`ing a name -- the allocator will not tell you. The
same trap in the `\csname` namespace is `dtRS`, in §4.

**`\ifx` compares catcodes.** `\pdffiledump` yields catcode-12 characters while
a hex table built from source produces catcode-11 letters for A-F. Use
`\pdfstrcmp`.

**Every line end inside an `\edef` is a space token.** Two stray `\fi` line
endings tripled the width of every ASCII row.

**`\or@` is a control word** when `@` has letter catcode.

**Plain TeX's `\loop` cannot nest** -- all loops share one `\body`. Use the
tail-recursive `\dtWhile`, which consumes its `\fi` before recursing.

**The page builder must stay neutralised.** `\output={\deadcycles=0
\setbox0=\box255 }` -- otherwise stray material emits blank pages.

**`\dimexpr` cannot hold map coordinates** despite scaled points being 16.16;
dimensions cap at 16383.99998pt.

**`\read16` gets EOF under `-interaction=nonstopmode`.** Interactive mode needs
TeX's normal mode.

**Anything after `\input doom.tex` is dead code.** The file ends with `\end`.
`\DTHOOK` is the only way in.

**A real `PLAYPAL` is not sixteen ramps of sixteen.** The built-in palette is,
by construction, so `base + shade` works. Doom's real palette is not: rows 9,
10 and 14 each hold *two* eight-entry ramps, and rows 0 and 15 mix unrelated
colours. The renderer emits shades 0..13, so a surface based on one of those
walks off the end of its ramp and jumps in brightness halfway down. Invisible
on the built-in map and on `test.wad`; only a real IWAD shows it. Usable bases
are 16, 32, 48, 64, 80, 96, 112, 128, 176, 192, 208.

**This constraint has now retired for every surface, and only fixtures keep
it alive.** Walls carrying a `TEXTURE1` composite and planes carrying a flat
both shade through `COLORMAP`. `\dtRampW/F/C` and the eleven usable bases
survive because they are what a render *without* a WAD uses, and because two
of check.py's oldest invariants are stated about procedurally shaded
surfaces -- which is why the ramp check runs against a `\DTNOTEX` render and
the wall-artwork check runs against a `\DTNOFLAT` one. Keep the ramps
correct: they are no longer on the screen for a real IWAD, but they are still
the control that several checks measure against.

**A texture name is not case sensitive, and real maps rely on that.** MAP01's
sidedefs spell `doortrak` and `support2` in lower case while `TEXTURE1`
spells both in upper. Comparing the raw bytes drops exactly those two
textures, and because an unresolved name falls back to the procedural bricks
rather than failing, the map still renders and still looks entirely
plausible. `\dtUpHex` folds both sides; there is a check on the count.

**`check.py`'s `edges()` is independent of *shade*, not of ramp assignment.**
It keys on the high nibble and records boundary *positions*, so changing a
sector's ramp is usually harmless -- but it breaks the moment two adjacent
surfaces that previously differed become equal, or vice versa. The first three
entries of `\dtRampW/F/C` in `wad.tex` deliberately reproduce the built-in
map's own sector ramps, which is what makes the WAD-vs-built-in comparison
possible. **Keep them.**

**pdfTeX hard-wraps terminal output at `max_print_line`** (79 by default),
mid-token, without inserting or dropping characters. Any harness that greps the
log for a `\message` must delete newlines first, or it silently misses whichever
frames straddled a wrap and reports a partial result as a pass. `check.py`'s
`track()` does this.

**Point location always succeeds.** Descending the BSP returns a leaf for any
coordinate, inside the map or not, so "which sector am I in" cannot detect that
the player has escaped. Void pixels in the render can.

---

## 7. What has been built, and why it is shaped that way

### Point location

`\dtPointIn§ec` descends from `\DTROOT` until the child is `>= 32768`.
Subsectors do not store their sector, so `\dt§ecSector` recovers it from the
first seg exactly as Doom does: `side = dtGs<seg>`, then `dtLF` or `dtLB` of
`dtGl<seg>`. This is also what places things on the right floor at load time.

### Floor following

`\dtFollowFloor` halves the remaining gap each frame, converging in two or
three; dividing by three took seven and read as floating up a ramp. MAP01
turns out to be the staircase test map -- walking north from the player start
climbs 8 -> 16 -> 24 -> 32 -> 40 -> 48 -> 56 with the eye tracking each step.

### Collision

Against **segs, not linedefs**: segs already carry vertex indices and a linedef
index, so this needed no new arrays and no node bounding boxes.

**It tests a move, not a position.** The first version tested only where the
move landed, which tunnels: a 40-unit step starting 20 units in front of a wall
ends 20 units behind it, both outside the 16-unit radius, so nothing fires and
the player ends up in the void -- where point location still reports a sector,
which is what makes it look like a "strange in-between space" rather than an
obvious escape. The fix adds a segment-crossing test between the move and the
seg. Because a sealed map cannot be left without crossing some seg, **the
crossing test is what keeps the player in the level**; the radius test at the
destination is what stops them resting inside a wall.

**Do not add a distance test at the move's origin.** After a slide the player
rests exactly at the radius, and that test would then block every subsequent
move and glue them to the wall.

Distance to a seg avoids a square root: bounding-box reject first (which is
also what keeps products under 2^31), then compare against the nearer endpoint
when the projection falls outside, or `|cross| < radius * length` when inside,
using the `dtGn` length computed at load.

Sliding keeps whichever single axis is free. Against an axis-aligned wall that
is exactly the projection onto the wall; against a diagonal it loses speed but
never sticks. Doom projects properly, which needs the blocking seg's direction
threaded back out of `\dtBlocked`.

### Things

`\dtSplice` replaces a band inside an already-assembled column, fully
expandably. Things are collected per frame, selection-sorted far to near, and
spliced.

`\dtThingClass` approximates Doom's `mobjinfo` with type ranges -- monster,
barrel, health, armour, ammo, scenery -- and `\dt@thingband` draws a
distance-shaded billboard with a tapered top. **Those two macros are now only
the fallback**, for no WAD, no sprite lump, or a type outside `dtSN`. The
built-in map is entirely on that path, which is what keeps every fixture in
§1 that predates sprites still meaningful.

### Sprite occlusion

`\CD` is one depth per column and answers exactly one question: is this sprite
behind the wall that closed this column. A portal *never* closes a column, so
until a door had shut completely nothing was hidden by it at all -- a door one
frame into opening drew its whole slab and every sprite behind it came through,
and a sprite behind a raised platform showed through the platform.

Doom keeps a drawseg per wall with its own clip arrays and a silhouette flag.
The equivalent here is `dtZT<x>`/`dtZB<x>`: every narrowing of `CT`/`CB`,
recorded with the scale at which it happened. Traversal is front to back, so
within a column those lists are **already ordered near to far** -- the clip in
force at a given depth is the last record nearer than it, so the lookup is one
forward walk with no sorting and no search. Only a real narrowing is recorded,
so ordinary geometry keeps these one or two entries long.

`\CD` is kept as the early-out: a sprite wholly behind the closing wall never
touches the lists.

### Sprite graphics

Read per map, not per WAD: a full sprite set is 1381 lumps and a map uses a
dozen, so `\dtWantSprite` collects the keys the placed things need and
`\dtLoadSprites` resolves all of them in **one pass** between `S_START` and
`S_END`. `\dtFindLump` scans the directory from a given entry and this WAD has
2928 of them, so a lookup per sprite would cost more than the entire map load.

Matching compares only the **first six bytes** of the name. That covers both
spellings in one test: `POSSA1` is NUL-padded past byte 6, and the lumps that
serve two frames mirrored carry the second in bytes 6 and 7 (`BOS2A1C1`).
Rotation 1 is the view of a thing facing you, so using it at every angle gives
sprites that always turn to the camera; rotation 0 means the picture is the
same from all sides, which is how every pickup is stored.

**Do not read pixels a byte at a time.** A post's run is one `\pdffiledump`
and is stored exactly as it comes out of the file -- with a real `PLAYPAL`
installed those bytes already *are* the palette indices the PDF wants, so
nothing is decoded on the way in or on the way out. Byte-at-a-time would be one
file dump per pixel.

The resampler is **source driven**: `\dtSprRun` walks a post's pixels once and
emits each into however many destination rows it covers, which is what `\Rep`
already produces. Destination driven would need random access into a run that
is a plain string of bytes. It also costs O(source height) per screen column
instead of O(screen height), and the column count already falls with distance,
so a distant sprite is a few columns of 56.

The clip is applied **per source pixel**, and the counts telescope to
`min(y1,hi) - max(y0,lo)` exactly -- which is what lets `\dt@onepost` splice
back a band it computed independently. If that ever stops being true the
column-length invariant catches it in aggregate; it caught an off-by-one in
127 of 800 columns when the count was deliberately broken.

### Pickups, and why the bar needed them

The status bar was the next thing on the roadmap and it was the wrong thing to
do on its own: numbers nobody can change are decoration, and a bar showing a
constant 100 proves nothing about the code that drew it. Pickups give every
widget on it a subject, and they cost less than they look -- a per-thing flag
on the `dtLU` precedent, a box test on the thing list, and a table.

**`\dtGOT` is `\dtFIRED` again.** Doom's give functions return a boolean and
the item only disappears if something was actually given; a medikit walked
over at full health stays on the floor. Every give routine here sets `\dtGOT`
and `\dt@give` marks `dtHG<i>` only if it came back set. Drop that and the
map still plays: things vanish when touched, which is what a pickup looks
like, and only a fixture that walks a full-health player over a medikit and
then counts pixels can tell the difference.

**Doom's reach is a box, not a circle**, and it has a height half. The
footprint test is `|dx|` and `|dy|` against the sum of the radii (20 + 16 =
36), and objects must also overlap vertically. Things here carry the floor of
the sector they stand in rather than a live z, so the height test compares
that against the floor under the player. Without it a stimpack on a ledge is
collectable from underneath, which no stock map is laid out to reveal.

**The item table is `\dtSpriteType`'s shape on purpose**: a kind and two
payload fields per thing type, and an absent entry means "not collectable".
That is what keeps every monster, corpse and column out of the scan without a
single exclusion, and it is why the built-in map -- whose things are all added
by fixtures -- was unaffected until a fixture asked for a stimpack.

The timed powerups are declared nowhere, deliberately. Nothing here damages
the player and nothing counts down, so collecting an invulnerability sphere
would delete it from the map in exchange for nothing at all. They stay put
until there is something for them to do.

### The status bar

Doom lays the bar out in a 320x200 screen, so everything scales by
`\SCRW/320` and `\SCRH/200` and the source can use Doom's own coordinates:
right edges at 44 (ammo), 90 (health) and 221 (armour), all at y 171, and the
face at 143,168.

**`V_DrawPatch` subtracts a patch's own offsets from the position it is
given**, and `\dt@hudpat` does the same. This is not pedantry: the face
patches carry (-5,-2) and the digit `1` carries (-1,0), so without it the face
sits five columns from where Doom puts it and every 1 is a pixel out.

**A number's pitch is the width of the digit ZERO, for all ten digits.**
`STTNUM1` is 11 wide against the others' 14 and Doom advances by 14 anyway,
which is what keeps a column of numbers aligned and `111` the right width.
Using each glyph's own width still draws every digit correctly and still
right-aligns them; it just puts `111` three pixels too far right. There is a
check, and it reads the 14 out of the WAD rather than believing this
paragraph.

**The face is two questions and only one of them is about pixels.** Which
face is arithmetic -- `ST_calcPainOffset` spreads five faces over 0..100
health, dividing by 101 so that full health lands on face 0 -- and a wrong
threshold still draws a perfectly good face. So `\dt@facepick` and
`\dtFaceKey` are separate from the drawing, and the fixture asks for the
*name*. That is also what makes the `\divide` rule visible: at exactly 80
health the quotient is 100/101, which rounds to 1 and truncates to 0.

The three glances within a pain level are Doom's random look left, ahead and
right, reshuffled every `ST_FACETIME` (17) tics. `\dtANIM` stands in for the
random number, which makes it deterministic -- the same argument the per-thing
animation phase makes. **Note the consequence**: the bar is not static over a
run longer than 17 frames. The M9 check that asserts it does not move is sound
only because its fixture renders 8.

**The weapon in your hands follows `\dtWEAP`** through `dtUS<n>`, and all nine
weapon sprites are ordinary lumps between `S_START` and `S_END`, so they ride
the sprite pass that was already happening and cost one want each.

### COLORMAP

34 tables of 256 bytes; 32 light levels are loaded, keyed as
`dtCM<light>@<HH>` by the two hex characters of the source byte, because that
is the form sprite pixels arrive in. Row 0 is the identity.

Every surface on a real IWAD now shades through `COLORMAP`; the palette-row
constraint in §6 applies to procedurally shaded surfaces only, which on a real
IWAD means none of them. `check.py` therefore runs both the ramp check and the
void-pixel check against a render with `\dtDrawThings` stubbed out and
`\DTNOTEX` set: real artwork is whatever colour the artist used, including
palette row 15, which the void check reads as the never-closed filler.

### Doors

A door is a sector whose ceiling animates. Nothing in the renderer or collision
needed telling, because both already read `dtSc` every frame: the ceiling
descending closes the view and the `P_LineOpening` headroom test at the same
moment. What *was* missing is that `\dt@portalcol` recorded no depth when a
portal's opening collapsed, so a shut door drew as a wall but every sprite
behind it passed the depth test and drew through. That is one line, and it is
the reason the door work and the sprite work had to land together.

**Door surfaces.** A door drawn from the front sector's `dtSw` with the brick
table is a door you cannot see. With no `TEXTURE1` composition yet the cue has
to be structural rather than pictorial: `dtD<u>@<v>` is the same 64 x 65 shade
table as `dtT` but banded vertically -- ribbed panels, a centre seam, a frame
edge, one rail at the tile join -- and `\WallRun` picks between them through
`\dt@tx`, a macro read during the `\edef` that expands the span and nowhere
else. Measured at 0.985 of the old 320x200 time, i.e. free.

Which surface is a door needs no new map array, because it follows from the
`dtDT` flag `\dtFindDoors` already sets. In Doom a shut door is the *upper*
texture of a two-sided line whose back sector has collapsed its ceiling onto
its floor, so the face is any seg whose **back** sector is a door; the track is
the door sector's own one-sided walls, so it keys on the **front** sector.
Both are decided once per seg in `\dtSegDraw` -- the two `\ifcsname` lookups
would otherwise run for every column the seg covers, and neither answer can
change across a seg. The face shrinks upward on its own as the door opens,
because the span already ends at the animated `dtSc`.

`\DOORRAMP` (128) and `\TRAKRAMP` (192) are counts, so a fixture can move
them onto rows the map under test never uses -- which is the only way to prove
a door pixel came from the door selection rather than from a sector that
happened to share a ramp. Keep them among the eleven usable bases in §6.

### Real wall textures

A Doom texture is not a picture but a recipe: `TEXTURE1` gives a size and a
list of patches with offsets, `PNAMES` turns a patch number into a lump name,
and the patch is the same column/post format `\dt@readpic` already reads for
sprites. Compositing at load into a flat `dtX<t>@<u>@<v>` array is what keeps
the inner loop from having to know any of that.

**Per-texel storage was a measurement, not a guess.** MAP01 needs 39 textures,
55 patches and 465k texel writes; pdfTeX defines 400k control sequences with
names of that shape in 0.6 s, so the array is affordable and the alternative
-- storing texture columns as strings and resampling them source-driven, the
way `\dtSprRun` does -- would have been more code for a slower inner loop.
Only 2 of the 39 have holes, which is why `\dt@fillholes` is gated on a texel
counter rather than run always.

**The sampling is a nested `\csname`.** Scanning the outer COLORMAP name
expands the inner texel lookup, which yields the two hex characters that
finish the key:

```tex
\csname\dt@cml\csname\dt@ux\number\numexpr(#2)/65536\relax\endcsname\endcsname
```

A composited texel already *is* a palette byte, so unlike `\WallRun` this
needs no `\hx` on the way out, and the two paths cost about the same. It also
means an undefined texel is a `Missing \endcsname`, not a wrong pixel -- hence
the hole fill, and hence the identity `COLORMAP` row built when a WAD has
none.

**`\dt@wrun` is `\let` to one run or the other**, so the `\edef` that expands
a span does not know which it is using. Both take the ramp base as their
fourth argument; `\WallTex` ignores it.

**The u offset is added after the perspective divide.** `\dtUOFF` is the seg's
offset along its linedef plus the sidedef's x offset. Folding it into
`\u@one`/`\u@two` instead would be the obvious place and would overflow:
`\fmul` there multiplies u by the scale, and a 256-unit offset on a long seg
pushes the product past 2^31. By the time `\rd` exists it is already in map
units, so adding it is both exact and free.

**Pegging is not cosmetic.** Doom pegs a texture to one end of its surface and
lets the other run off, and the linedef's two flag bits pick which. The
default for an upper is the *back* ceiling, which is what makes a door's slab
travel upward with the ceiling instead of standing still while the gap opens.
Deleting the pegging lookup moves 7640 pixels of an eight-frame walk.

**Shading is Doom's own light table**, near enough: `lightnum` is the sector
light's top four bits, `startmap` is `(15-lightnum)*4`, and the distance term
is the scale index halved. The `320/\SCRW` factor is Doom's own
`SCREENWIDTH/viewwidth`, so a narrower view fades over the same world distance
rather than over the same number of columns -- and the scale has to be
converted *before* the shift by 12, or the index quantises to steps of three
at 100 columns wide. Fake contrast (east-west walls a level darker,
north-south a level lighter) is the largest single contributor to how much
this looks like Doom: stubbing it moves 84k pixels.

**What is not done here:** masked middle textures on two-sided lines. MAP01
names `MIDBARS3` on one, and it composites correctly, but nothing draws a
middle texture on a portal, so the holes never matter.

**This milestone deliberately changed the built-in map's pixels**, which is
worth knowing before anyone bisects against git history looking for a
regression. The old renderer hardwired every upper texture to start at the
*front* sector's ceiling -- that is `ML_DONTPEGTOP` behaviour, applied to
every wall in every map whether the flag was set or not. Doom's default is
the opposite: with the flag clear the texture's *bottom* is pegged to the
back sector's ceiling. `\dt@pegup` now follows Doom, so portal uppers shift,
and the built-in map and `test.wad` are no longer pixel-identical to the
pre-texture tree. Nothing else moved: forcing `\dt@pegup` back to the old
single form restores byte-for-byte identity on both, which is how this was
established rather than assumed. The `\dtEdges` comparison between the WAD
and the built-in map is unaffected, because it keys on the ramp.

---

## 8. Roadmap

### Done: the interactive-mode token leak

It was not a leak, and it was not in the sprite pass. `\dt@thingrot` built the
rotation digit into `\dt@tr`, and `\dt@tr` is *also* play.tex's
column-to-row transpose walker. So the engine's per-thing scratch overwrote a
macro the ASCII path had defined once at load, and
`\expandafter\dt@tr\dt@col\relax\relax` expanded to a bare `8` and left the
whole column standing in vertical mode as text -- one Overfull hbox per
column, zero errors, and a frame that still looked right.

The engine's macro is `\dt@trot` now. The `\vbox` is gone from `\dtKeyLoop`:
keep the call bare, because a box there would swallow the next such bug in
silence, and check.py now asserts that interactive mode typesets nothing.

**The old bisection in this section was wrong, and wrong in an instructive
way.** It read:

| stub | Overfull |
|---|---|
| `\def\dt@onepost#1#2#3{}` | 0 |
| `\def\dt@sprcol{}` | 102 |

which is impossible, since the second calls the first. The fixtures defined
those stubs with `@` at catcode 12, so `\def\dt@onepost#1#2#3{}` defined
`\dt` with the parameter text `@onepost#1#2#3` and never touched the macro it
named. **A stub that does not take effect looks exactly like a stub that
proves the code is not involved.** Set `\catcode`\@=11` in the fixture and
re-run before drawing any conclusion from a bisection over `\dt@` names.

What actually located it, in about ten minutes: probe `\ifhmode` after each
stage of `\dtRenderScene` and each stage of `\dtKeyLoop`. Horizontal mode was
never entered during the scene build at all -- it started inside
`\dtAsciiFrame`, one Overfull per column with `\SCRW` columns. From there
`\meaning\dt@tr` at the point of use said `macro:->8` and the bug was over.
**When a macro misbehaves, print its `\meaning` at the point of use before
theorising about expansion.**

A name-collision audit across the three files found one other pair,
`\dt@k` (play.tex's key, wad.tex's sprite-key scratch); they do not overlap in
time today, but play.tex's is `\dt@pk` now.

### Done: frame animation

`\dtSpriteAnim{type}{letters}{tics}` cycles a thing type through `letters`,
holding each for `tics` frames; `\dtANIM` advances once per rendered frame in
both the batch loop and the key loop, next to `\dtDoorTick`. A type with no
entry keeps its single `\dtSN` frame and costs one `\ifcsname` that misses.

Three things are worth keeping in mind if this is extended:

- **The letters are written out, not derived as base+n.** Doom's sequences are
  not all runs: a bonus goes `ABCDCB`, up the letters and back down. A picker
  that assumed a run would ask for `E` and `F`, get nothing, and drop the
  thing to rotation 0.
- **The phase is the thing's own index.** Doom perturbs the first tic count
  with `P_Random` at spawn so torches do not flicker in lockstep; the index
  gives the same staggering, is deterministic -- which the harness needs --
  and costs no array.
- **The modulus is `\divide`, not `\numexpr`.** `\numexpr` rounds, so 5/6
  becomes 1 and every frame arrives a tic early once per cycle. There is a
  check on exactly this.

The loader had to change too: `\dtWantSprite` is called for every letter in a
sequence, not just the one in `\dtSN`, or `\dt@animframe` asks for a picture
that was never read. MAP01 went from 27 pictures for 13 keys to 50 for 22.

Both places that walk a sequence hit the same trap, and it is the one in §6
about `\dtSpliceCol`, from the other side: **macro arguments are not expanded
while being grabbed, even inside an `\edef`**, so passing
`{\csname dtMA..\endcsname}` hands the picker the `\csname` token itself as
its first letter. Build the call in an `\edef` with the macro `\noexpand`ed,
so the sequence expands in place before anything grabs it.

Still on frame A of the spawn state: nothing that *changes* state. This is the
tick enemies will reuse -- what is missing for them is a per-thing current
state rather than a per-type sequence.

### Done: the weapon and status bar

`\dtBlit` is the sprite path with the box set by hand and the clip opened to
the whole column: screen space, no projection, no depth test. Both pictures go
through `\dt@readpic`, and `STBAR` is not a sprite, so `\dtLoadGfx` finds it by
name in the whole directory.

The placement is not guesswork -- a weapon patch's offsets *are* its placement.
Doom puts the left edge at `-leftoffset` and the top row at `-topoffset` in a
320x200 view; for the pistol that is 126 and 106, and 106 plus its 62 rows is
exactly 168, flush with the top of the bar. Verified against a render: changed
pixels begin at row 106 and span columns 126..182.

Horizontal and vertical scale are passed separately because the default
200x120 is not 320x200's aspect. A `STBAR` wider than 320 is a widescreen one
and is anchored by its overhang so its middle 320 crosses the view, rather than
being squashed to fit.

Nothing on the bar is filled in: no numbers, no face. Those are `STTNUM*` and
`STFST*`, more pictures in the same format.

### Done: sprite rotations

The rotation is the angle from the viewer to the thing less the thing's own
facing, biased by half a sector (`+4608` in 8192ths) so rotation 1 is *centred*
on facing you rather than starting there. `\dtAtan` folds onto an eighth-turn
table built from the sine table at load.

Four of the eight rotations are stored as the mirror of another and share a
lump: `POSSA2A8` is rotation 2 and rotation 8 flipped. So the loader keys a
want entry on **name plus frame letter only**, splits each directory name into
its three pieces, and reads the rotation digit back off whichever slots match
-- rotation characters are '0' to '8', hex 30 to 38, so the low nibble *is* the
digit and needs no decoding. `dtPf<key><rot>` marks the mirrored ones and
`\dt@sprcol` reverses the column order; nothing below that line knows.

### Done: real sprite graphics

All three of the things this section said to plan for turned out to be the
right three, and §7 records how each landed: whole-region dumps, a source-driven
resampler, and one splice per post. The thing-type table grew past the
hardcoded dozen to cover the monsters, pickups, keys and scenery a Doom II map
uses, keyed on four characters *plus a frame letter* -- corpses lying around a
map are a late frame of a death animation, not frame A.

### Done: lifts, switches, and the rest of the sector machinery

Doors proved the pattern and it held: a lift really is a door with `dtSf`
where the door machine has `dtSc`, and it needed nothing from the renderer or
from collision, because both already read `dtSf` every frame.

**Doom's special numbers are not a list, they are six fields in a trench
coat.** The table that decodes them is the whole of this feature; everything
else is two state machines and three scans. `\dtLineAction{n}` sets `\dtTKIND`
(ceiling or floor), `\dtTFUN` (which motion), `\dtTACT` (use by hand, use as a
switch, or walk over), `\dtTREP`, `\dtTBYTAG` and `\dtTSPD`. 51 types are
declared as `\dtActDef` lines; anything undeclared leaves kind 0 and is inert,
which is how the built-in map and every unimplemented special come out of it.

It is a csname per special rather than an `\ifcase`, for two reasons: the
range is sparse up to 123, so an `\ifcase` is a hundred branches to miscount,
and a chain of `\ifnum` would run the whole chain on every miss -- and this is
consulted once per seg by the use scan and again per seg by the crossing scan.

Four things are worth knowing if this is extended.

- **The tag is the point, and it is not the geometry.** A manual door acts on
  its own back sector; everything else acts on every sector carrying the
  line's tag, however far away. MAP01's switch 84 names four sectors at once,
  which is what makes "acts on the back sector" and "acts on the tag" tell
  each other apart -- three of the four staying put is invisible to any
  aggregate count. Tag 0 must match nothing: it is what an untagged linedef
  and an untagged sector both report, and letting those meet attaches every
  manual door in the map to every plain sector in it.
- **Targets are computed at marking time, not at activation.** This follows
  the door precedent and keeps firing to pure state assignment. It differs
  from Doom only when two specials name one sector with different targets;
  the first one marked wins. No stock map in the IWAD does this, but a PWAD
  could, and that is where to look if a mover goes to the wrong height.
- **`\dtFIRED` is Doom's `EV_` return value** and is load bearing in a way
  that is easy to drop. The switch face only changes and the once-only line is
  only spent if something actually moved; a tag naming no sector, or naming
  one already in motion, leaves the trigger armed. It is also what stops a
  held use key strobing a repeatable switch once a frame.
- **A trigger acts only on an idle sector; the use key still reverses.** That
  asymmetry is deliberate. Making triggers idempotent is what lets the
  crossing scan ignore the fact that the BSP splits a linedef into several
  segs and will report the crossing once per seg.

**Switch faces are recovered from the name.** Vanilla has no `SWITCHES` lump
-- the pairing lives in the engine -- but what the engine holds *is* the
naming convention, so `SW1x`/`SW2x` are wanted together at load and recorded
in `dtXs` pointing at each other. Doing it at load rather than at press time
is what makes the pressed face exist at all: only names a sidedef mentions are
ever composited, and a map naturally mentions only the unpressed one. MAP01
goes from 40 composited textures to 42. Comparison is character by character
with `\if`, not `\ifnum`: a name whose hex contains A-F is not a number and
`\ifnum` raises an error on it, which `JOINT2` would.

**The bug this feature exposed was not in this feature.** `\dtFollowFloor` is
reached only from `\dtTryMove`, so a player who is not walking never settles
against the floor -- and a descending lift left the eye hanging at the height
it started from, with the floor visibly dropping away underneath. No error, a
perfectly steady frame, and nothing in the harness could see it, because until
now no floor could move. `\dt@platride` settles the eye in the two branches
that actually moved a floor, rather than once a frame in the main loop:
`\dtFollowFloor` halves the remaining gap, so a second call in a frame the
player also walked converges at a different rate and would shift every
view-height expectation in the harness. A map with no moving floor cannot tell
the difference.

Not done: `\dtCrossLines` runs the whole seg list per move rather than the
blockmap, gun-activated (`G`) lines are inert, and the once-only flag is per
linedef rather than per line-and-activation. `dtHz` is no longer quite fixed
at load -- `\dt@monstep` rewrites it for a monster that walks onto a
different floor, which is the one exception -- but nothing re-reads it when a
*sector* moves, so a thing standing on a lift still does not ride it. Locked doors still open without
keys. Special 11 (exit) is inert, which is why the scripted walk on MAP01 does
not end the level.

### Done: pickups and the status bar

The bar was listed here as the next thing to do and it needed pickups first;
§7 records why, and the shape of both. Four things are worth knowing if this
is extended.

- **The loader needed a third resolver, and it is the second one of its
  shape.** The bar is 28 pictures -- ten digits, a percent, fifteen faces, the
  corpse and the bar itself -- and `\dtLoadGfx` is a full directory scan each,
  which is about 79000 file dumps against the 6000 the whole map load costs.
  `\dtWantGfx` marks names and `\dtLoadGfxAll` walks the directory once,
  exactly as `\dt@wantpatches`/`\dt@findpatches` do for patches and
  `\dtLoadSprites` for sprites. Measured against the pre-M14 tree in the same
  sitting: 3.68 s against 3.57 s, i.e. free. **Any new picture belongs in that
  pass, not in a `\dtLoadGfx`.**
- **A given weapon comes up in your hands only if it is new.** Doom returns
  true for the ammunition alone when you already own it, so walking over a
  second shotgun is worth eight shells and nothing else. Getting this wrong
  drags the weapon back into your hands every time you cross it.
- **The backpack doubles the maxima once**, then gives a clip of each --
  including of a slot that was already full, which is why the doubling has to
  happen first. Doubling per pickup is invisible on every stock map, since
  they carry one.
- **Nothing here damages the player yet**, so health only ever rises and the
  face only ever heals. When damage lands, the face grows the rest of its
  states -- the ouch, the grin after a kill, the turn toward whatever hit you
  -- and `STFDEAD0` stops being unreachable. `\dtFaceKey` is where they go.

### Done: the rest of the bar

All four of the things listed here as missing are in: the ammunition boxes,
the arms panel, the key icons, and the face's damage states -- the last of
those in M15, because they needed something that could hurt you. It was
indeed more pictures in a format already read at positions already known, and
twenty of them went through the same single directory pass, taking the bar
from 53 to 73.

Three things are worth knowing.

- **`\dt@hudnum` grew a font prefix**, because the small numbers are the same
  drawer with `STYSNUM` in place of `STTNUM`. The pitch is still the width of
  whichever font's own ZERO, which is the rule M14's layout checks actually
  pin -- they are not statements about `STTNUM`. `\dt@hudnum` keeps its name
  so those checks go on naming the macro they were written about.
- **That move broke a mutation, silently.** `digit-pitch-per-digit` targeted
  the literal `dtPwSTTNUM0` line; once it became `dtPw#10` the mutation
  reported `MUTATION DID NOT APPLY`, which prints as a line of output and not
  as a failure. It has been repointed. **When you move a line, grep
  `mutate.py` for it** -- a mutation that stops applying is a check that
  stops being watched.
- **The arms panel reads `dtUW`, not `\dtWEAP`**, and the difference is the
  whole check: `STARMS` is a background that draws either way, so what has to
  move is the six numbers on it. The fixture that holds a shotgun without
  owning one is what tells the two apart.

Not done: the key icons cannot tell a card from a skull, because `\dtItem`
gives both spellings of a colour the same key number and `dtUK` keeps only
that. Doom draws them in the same slot, so the bar looks right and is missing
information rather than misplacing it.

### Done: enemies and combat

All three of the things this section said to plan for were the right three: a
per-thing state machine, `\dt@segcross`'s arithmetic borrowed for sight, and
`P_TryMove` for movement. §5 records the shape. Six things are worth knowing
if this is extended.

- **The states own the frame letter, so the loader had to grow again.**
  `\dt@thingkey` picks `\dt@monframe` over `\dt@animframe` for anything
  carrying a `dtOs`, and `\dt@wantstates` wants every letter of all five
  sequences rather than the one in `\dtSN`. That is `\dt@wantall`'s lesson
  one level up, and it takes MAP01 from 50 pictures to 156 -- see §9 for
  what that costs.
- **A corpse is a state with no letters of its own.** `\dtMonAnim` declares
  `@5` as a copy of `@4` for that reason, and a state left undeclared is not
  a missing picture but an expansion accident. §4 has the detail; the
  symptom was 101 errors, none of which named the table row.
- **Speed is per frame, not per tic, and it is a deliberate departure.**
  Doom moves a monster `speed` units per `A_Chase`, on a walk frame lasting
  three or four tics -- about two units a tic against a running player's
  sixteen. This engine's frame is a whole 40-unit player step, so moving a
  zombieman its eight units in one frame leaves the *ratio* nearer Doom's
  than a faithful per-tic reading would. `\dtSpriteAnim` reads Doom's tics as
  frames for the same reason. Anything added here should think in ratios.
- **A_Look runs on a frame change, not every tic**, which is where Doom puts
  it too -- and it is the difference between one sight scan per monster per
  ten frames and 29 scans of 601 segs every frame. The spawn counter is
  staggered by the thing index at spawn, which both reproduces Doom's
  `P_Random` perturbation and spreads those scans across frames.
- **The attack fires on the sequence's last letter, not after it.** Doom
  fires on a named frame; waiting for the wrap put a whole attack animation
  between the decision and the damage, which reads as a monster that raises
  its rifle and never pulls the trigger.
- **`\dtSHOT`, not `\dtFIRED`.** The trigger's "did anything happen" flag is
  its own register, because `\dtFIRED` belongs to the linedef machinery and
  `\dtUse` writes it. Same argument as `\dt@trot` and `\dt@pk`, in the
  register namespace.

### Done: ceilings, stairs, lights and the exit

Thirty more `\dtActDef` rows, one more state machine and no new ideas, which
is what M13's table was built for. Four things are worth knowing.

- **A staircase is the one thing whose targets cannot be settled at marking
  time.** Every other mover's heights are a property of the map's shape; a
  staircase is a *chain*, and which sector is the third step depends on
  which one the trigger named as the first. So `\dt@firestair` walks the
  chain when it fires and hands each step to M13's moving-floor machine --
  `dtES=3` with `dtEH` set to that step's own accumulated target. A stair is
  a lift told to go up and stay there, and reusing it that way is most of
  why this feature is short.
- **`dtSU` is `dtLU` for sectors.** Without a "already part of a staircase"
  flag a chain that loops back on itself builds a tower; `\STAIRMAX` is the
  belt to that braces.
- **`dtAf` is ABSENT on a map with no flats**, so the chain rule reads it
  through `\dtSecFlat` the way tags are read through `\dtLineTag`. On the
  built-in map everything reports -1, every neighbour matches, and a
  staircase spreads through all three rooms -- which is the right answer for
  a map with no flats to tell its floors apart with, and it is what makes
  the fixture possible at all.
- **"Stop" is the one type that acts on a sector that is not idle**, and it
  has to be, because that is its whole job. Every other trigger here refuses
  a moving sector, which is what makes firing one twice in a frame the same
  as firing it once and lets the crossing scan ignore a linedef the BSP
  split.

Not done: Doom's strobing and glowing lights, which need a per-sector thinker
where these four need an assignment; the "raise floor to nearest" family; the
donut; and teleporters. The exit records that it was reached and stops the
run, because there is nowhere to go.

### Done: projectiles

The section this one used to say everything was waiting on. It is the first
thing in the engine that **exists without being on the map**, and that is the
whole of what made it a milestone rather than a table: everything with a
position before this was placed by `THINGS` and outlives the run.

Six things are worth knowing if this is extended.

- **A missile is a thing, and that is most of why it is short.** It goes in
  the same `dtHx`/`dtHy`/`dtHz`/`dtHt` arrays, so projection, the back-to-
  front sort, occlusion, the silhouette clip and the whole picture reader
  needed no changes at all. What it needs beyond a thing is a velocity, a
  state and an owner. The one renderer change worth its line is that missiles
  are drawn fullbright, which `info.c` marks every one of their frames.
- **Slots are recycled, and `dtHG` could not do it.** A thing that is taken
  is gone permanently and `dtHG` says exactly that; a spent missile's slot
  has to be handed back. State 2 is that wait, `\NTHING` grows once to
  `\dtMAXMIS` past the map's own count, and sustained fire is bounded --
  without which a two-minute firefight leaves a thousand dead fireballs in
  every loop that walks the thing list.
- **`\dt@segopen` grew the one branch that is not a walker's.** A missile
  does not climb steps and is stopped by the opening at the height it
  arrives at, which is `P_TryMove` skipping the step test for `MF_MISSILE`
  and `P_ZMovement` exploding it against the floor instead. That is what
  lets a fireball fly over a parapet the imp behind it could not walk up.
- **Substepping is not optional here.** A frame is a 40-unit player step and
  a plasma bolt covers sixty of them, so a missile tested only at its
  destination crosses most of MAP01's walls between two samples. Doom splits
  a move longer than half `MAXMOVE` for the same reason and this splits at
  16 units, which is under every radius in the game.
- **The z step must be taken over the frame, not per substep.** `\numexpr`
  rounds away from zero, so a momz of -3 split over two substeps is -2 and
  -2: an imp's fireball descends a third faster than it was aimed, reaches
  the floor a stride in front of the player and explodes against it, every
  time. It still flies, still explodes, still animates and still draws. What
  it stops doing is arriving, and no count of missiles or explosions can see
  that -- only the player's health, and only if the fixture is arranged so
  that nothing else can take any off. `missile-z-per-substep` is the
  mutation.
- **Doom's missile speeds are per TIC and everything else here is per
  frame.** M15's note applies one level up: what has to look like Doom is
  the *ratio* of a missile's pace to the player's. A frame is a 40-unit step
  against Doom's running 16 units a tic, so `\dtMISRATE` is the 5 of that
  five-halves and the table keeps `info.c`'s own numbers.

**The loader asks one question and `dtJm` answers it for both halves.** "If
this thing is on the map, which missile pictures will be needed" is answered
by an imp because it throws one and by a rocket launcher lying on the floor
because the player will. That also means the answer follows the map's own
filtering: MAP01's plasma rifle and both BFGs are multiplayer-only entries
and are dropped with the deathmatch starts, so MAP01 wants `BAL1` and `MISL`
and *not* `PLSS`/`PLSE`/`BFS1`/`BFE1` -- 16 more pictures rather than 24.
A fixture that hands out a weapon the map does not carry gets no artwork for
it and falls back to the coloured billboard; the built-in map, which has no
sprite data at all, is on that path for every missile and it is where the
mechanism checks live.

Departures worth recording: the revenant's tracer does not home; the BFG's
forty tracers are an arc scanned from the player's position rather than
forty rays, which on a crowd is more generous than Doom and never less; and
a missile's spawn is placed clear of whoever threw it rather than nudged
half a tic forward and re-tested, which is `P_CheckMissileSpawn`'s intent
with one fewer move test.

### Done: masked middle textures

The design written up before it was built survived contact: keep the patch's
post chain for the textures a sidedef names as a middle on a two-sided line,
and draw them in the sprite pass, which already walks posts and already
clips to a per-column silhouette. The feature needed **no new inner loop at
all**. Four things are worth knowing.

- **`\dtSegDraw` had to be split, and the split is where `\dtPlaneSeg`
  already sat.** The masked pass revisits a seg after the wall pass is over
  and has to rebuild its sectors, sidedef, offsets and pegging without
  re-running the plane setup. `\dt@segsect` and `\dt@segtex` are those two
  halves; the order is unchanged and the built-in render is pixel-identical
  across the change, which is the only way to be sure of that.
- **Doom's own restriction is what makes this short.**
  `R_GenerateComposite` only keeps a masked texture's posts when it is a
  single patch, so every masked middle a commercial map uses is one. MAP01's
  two -- `MIDBARS3` out of `RW45_1` and `BRNSMAL1` out of `W111_2` -- are
  both one patch at (0,0). (That sentence about Doom is not quite right, and
  neither was the next one, which said anything else "keeps its opaque
  composite and draws as a wall": Doom keeps posts per COLUMN wherever one
  patch covers it, and here a middle with no posts was not drawn at all.
  M23 builds posts for any texture -- see "Done: the rest of the roadmap".)
- **A masked seg's depth is a range where a sprite's is a point**, so no
  single order is right for both: one end of a railing can be in front of a
  monster and the other behind it. It is sorted by its nearest scale, which
  is what Doom does and is wrong in the cases Doom is wrong in.
- **It is drawn once, not tiled.** `R_RenderMaskedSegRange` stands the
  texture on the bottom of the opening when the line is unpegged from the
  bottom and hangs it from the top otherwise, and either way one copy --
  which is why the wall path's `\dt@wrapv` is not wanted here and why the
  band is computed from two world heights rather than from a v wrap.

Not done: a masked middle on a multi-patch texture, and the middle texture's
own contribution to blocking a shot (Doom's `P_LineAttack` ignores it too).

### Done: locked doors, infighting and sound

Three separate gaps that turned out to share one shape: each is a thing Doom
carries on the mobj or the line that this engine had no room for because
there was only ever one of the thing in question.

- **A seventh action field with no seventh argument.** Six of the sixty-odd
  `\dtActDef` rows want a key, so widening the table would have been sixty
  edits and sixty chances to shift a column. `dtLK<special>` is a sparse
  second table with "absent means none", which is the shape `\dtLineTag` and
  `\dtLineSpec` already use. **The key test goes in `\dtFireLine`, before
  `\dt@fireact`** -- a refused door must not spend a once-only line and must
  not turn a switch face, and both of those live inside it.
- **Doom's comments in this file were wrong about two door numbers.** 33 is
  RED and 34 is YELLOW; the rows said the opposite, which cost nothing at all
  while no key was ever asked for and would have cost a wrong-coloured door
  the moment one was.
- **Infighting is not a mechanism, it is one line of `P_DamageMobj`.**
  `target->target = source`, and everything else follows: the tick reads
  `dtOt` instead of always the player, and a fireball that catches a
  zombieman in the back does the rest. What it needs beside it is
  `PIT_CheckThing`'s species rule -- a missile passes through a thing of the
  same type as its shooter -- without which the first fireball thrown in a
  room of imps starts a brawl Doom would never have started, and infighting
  stops being something the player provokes and becomes the default.
- **The tick now has three pairs of position registers**: `\dtPSX`/`\dtPSY`
  is wherever the point locator last looked, `\dtPLX`/`\dtPLY` is the player
  taken once at the top, and `\dtMTX`/`\dtMTY` is what this monster is
  fighting. §5 records what the second one cost; the third is the same
  hazard again and is why `\dt@monhit` saves `\dtMBI` **and** `\dt@mty`
  across `\dtHurtThing` rather than trusting the call.
- **Sound needs a graph, and the graph has to be built once.**
  `P_RecursiveSound` floods through sectors, and asking "which lines touch
  this sector" per sector per shot is 59 x 370 on MAP01. `dtSJ<s>` is that
  adjacency, built at load from the linedefs, and the flood is then a queue
  over sectors. The rule it enforces is Doom's exactly: cross any two-sided
  line that still has a gap, cross at most one `ML_SOUNDBLOCK`, and no
  distance limit whatever -- on MAP01 one shot wakes most of the level.
- **`\dtSpawnMonsters` grew a point locator, and that is a load-time echo of
  `\dt@monall`'s hazard.** It writes the registers the player's position
  lives in. Invisible in the engine's own call, which `\dtLocatePlayer`
  follows anyway; not invisible to the dozen fixtures that call it from
  `\DTHOOK`, which runs *after* that. It ends with a `\dtLocatePlayer` of its
  own now.

Not done: Doom's `P_NoiseAlert` also gives the woken monster the player as a
target through `soundtarget`, which this does; it does not model a monster
losing that target when it loses sight.

### Done: the weapon state machine, spread, and the z half of sight

- **The refire period is a rate, so it is scaled like one.** M18's note
  applies unchanged: Doom's periods are in tics and this engine's frame is
  two and a half of them, so the table keeps Doom's numbers and
  `\dtMISRATE`'s ratio converts. Reading tics as frames would make every
  weapon two and a half times slower than the man carrying it -- and the
  point of the feature is the ratio *between* weapons, which "one shot a
  frame" could not express at all.
- **Per-pellet aim is what makes the spread real**, and it is what M15
  recorded as missing: the count and the damage were Doom's and where they
  went was not. Seven pellets is seven aim scans and seven sight tests, and
  that is the price. The reading that says it works is that **one blast
  lands on two different things**, which a single shared aim could not do
  however wide the numbers were.
- **The pellet loop cost an hour to a shared register.** `\dt@aim` loops on
  `\dtSHN` over the thing list, and the pellet loop was written on `\dtSHN`
  too -- so the aim scan reset the pellet counter to zero and left it at
  `\NTHING`, and with more pellets than things the bound was never reached.
  Not a wrong answer: a run that never ends. §6's register aliasing, in the
  one place this week that nested a loop inside another.
- **The z test needs the fraction along the sight line**, and
  `\dt@sightcrossB` already computes it and throws it away: its two cross
  products are the sight line's own ends measured against the seg, and their
  ratio is exactly how far along the crossing happens. Both scalings are in
  `\numexpr`'s `a*b/c` form because the numerator alone reaches a map extent
  squared.
- **A sentinel is what made adding two z arguments safe.** `\dtSight` is
  called from five places and three of them already carry a tick's worth of
  state, so the ends travel in registers -- and `\dtNOZ` means "this caller
  has not said", which gets exactly the old behaviour rather than a ray at
  z=0 that every raised floor would stop. `\dtSight` puts the sentinel back
  when it is done, so a value cannot leak from one scan into the next.

Not done: no vertical autoaim, so a player's missile flies level and a
hitscan has no slope; Doom's `P_AimLineAttack` picks both.

### Done: the viewport, the pipe, and the tools

The first milestone here that adds nothing to the *simulation*. It is about
the two ends of the thing instead: what a player sees while playing, and what
survives afterwards.

**The premise was wrong and worth stating.** `\DTW`/`\DTH` had quietly become
two different things -- the render size in batch mode and the *terminal* size
in interactive mode -- because the ASCII path printed one character per pixel
and every other row. So playing meant a 76x60 render, and the recording of a
session was 76x60 too. `\DTASC{n}` separates them: the render stays whatever
you asked for and a terminal cell becomes the mean of an `n` by `2n` block.
The 2 is not a fudge -- it is the cell aspect the old code was already
spending, by throwing a row away instead of averaging it in.

Three things came out of building it that were not obvious beforehand.

- **The cheap direction is the surprising one.** A *smaller* viewport over
  the same render is faster, not slower: the per-pixel work is identical at
  every `n` and the string appends collapse with the viewport, against
  accumulators that are O(current length) per append. `\DTASC{4}` costs 50 ms
  of filter where `\DTASC{1}` at the same render costs 207 (§9). Decimating
  is what makes a high-resolution interactive render affordable at all.
- **The filter had to index by luma, and the array had to hold the number.**
  The old table mapped a palette byte straight to a character, which is fine
  when you are picking one and useless when you are averaging thirty-two.
  `dtLV<HH>` holds 0..255 and the character is chosen once, at the end.
- **Handing the frame over is cheaper than drawing it.** `\DTPIPE` writes the
  `dtFC<x>` macros the PDF already gets, which is one `\write` a column and
  no filter at all -- 13 ms/frame against the ASCII path's 23. So a front-end
  is not a luxury paid for in speed; it costs the engine less than the
  built-in renderer it replaces.

What the pipe buys is everything a TeX macro cannot decide: `play.py` reads
single keys with no Enter behind them, draws the frame in colour at twice the
vertical resolution one character per pixel manages -- or at *four* times it
in plain text, since Braille puts 2x4 dots in a cell -- and records demos
that replay exactly. `gif.py` turns a recording into a GIF without resampling
or quantising anything, because both ends are already indexed.

The resampling on that side is in RGB and has to be. Averaging palette
*indices* is meaningless on a real PLAYPAL: it is fourteen unrelated ramps,
not a gradient, so the mean of two indices names a colour with no
relationship to either and a downscaled frame comes out as confetti. It is
the same argument that made play.tex's own table index by luma rather than
by the low nibble, arrived at twice from opposite ends.

Not done: the pipe is one-way and one frame at a time, which is all a
turn-taking `\read16` loop can be. Real-time play would need the engine to
poll rather than block, and TeX has no way to ask whether input is waiting.

### Done: the rest of the roadmap (M23)

Everything the list at the end of §2 used to say was missing, in one
milestone, because by now almost none of it needed a new idea -- which is
itself the finding. Seven milestones of machinery had been built to be
extended, and this is what extending it cost.

- **A sector special is not a linedef special, and that is why M16 could not
  do the strobes.** M16's four lights are things a line DOES, once, to a
  sector it names: an assignment. A strobe is something a sector IS for the
  whole level, with nobody firing it. So it is a thinker -- a list of sectors
  ticked once a frame -- and once that list existed the burning floors, the
  secrets and the scrolling walls were three more things to hang off the same
  tick. The table of linedef specials did not grow a row for any of them.
- **Doom's flickering light is a single bit.** `P_SpawnLightFlash` sets
  `maxtime` to 64 and `T_LightFlash` computes `(P_Random() & maxtime) + 1`:
  an AND with one bit, so the light is on for one tic or for sixty-five and
  never anything between. Read as "a random time up to 64" it comes out as a
  strobe; read as written it comes out as a loose connection, which is what
  it looks like in the game.
- **A glow cannot be scaled the way a strobe can.** Doom steps eight a tic
  and bounces: it takes the step back when it overshoots. At twenty a frame a
  bounce pins any glow narrower than twenty units at one level for ever --
  down, back, up, back. It clamps to the end it reached and turns there.
- **Everything with a period went through the same ratio**, two frames to
  five tics, and for M18's reason: what has to look like Doom is a rate
  against the player's own pace. A burning floor every thirteenth frame, a
  strobe bright for two, invulnerability for 420.
- **Teleporting is writing two registers at the right moment.**
  `\dt@fireteleport` runs inside `\dtTryMove`, after the move is committed
  and before the point locator, the touch scan and the floor follow -- so
  replacing `\PX` and `\PY` there is the whole of it. What it needed that
  nothing before did is which SIDE of the line the move began on, and
  `\dt@segcross` had been computing that as the first half of its crossing
  test since M3 and throwing it away.
- **A rising floor was already written.** M16's staircase is "a lift told to
  go up and stay there"; a floor raised to the next one up is one stair.
  What kind 8 adds is four ways of asking where to stop, one neighbour walk
  with the question `\let` into it, and the decision to ask when the line
  fires rather than at load.
- **The fixed colormap costs no pixel anything.** The obvious implementation
  is a second lookup per pixel, or a pass over the finished frame. Neither is
  needed: every pixel on a textured surface already goes through
  `dtCM<level>@<byte>` with the level settled per column, per seg or per
  thing, so drawing the whole view through table 32 is five assignments in
  five places that were already choosing a table. The plane shader's clamp
  table was the one obstacle, and it became the mechanism (§5).
- **The BFG's forty rays were `\dt@aim` forty times.** M18 recorded that the
  spray was an arc because there were no tracers. There were: the aim scan a
  bullet runs is exactly "the nearest live thing this ray passes inside, then
  one sight test". Pointed along forty angles from the player it is
  `A_BFGSpray`, and a monster behind another is shielded for free.
- **A masked middle of several patches is the composite, run again.**
  `\dt@zclear` fills a scratch column with palette 0 so that a hole cannot be
  an undefined texel -- and thereby throws away exactly what transparency
  needs. Clearing to a two-character marker that is not hex instead, and
  gathering the runs between markers, is a dozen lines. Doom itself gives up
  here (the "medusa"); there was no reason to copy that.

Three things went wrong on the way and each is a trap in its own right:
`dtRS` (§4), the teleporter that fired twice (§5), and the harness helper
that read `4960` as `4` (§11).

**What is left** is in §2 and is short. The two monsters that need mechanisms
of their own are the honest gap: the arch-vile's resurrection wants a corpse
to remember what it was, which `dtOs` = 5 already does, so that one is
nearer than it looks; the pain elemental wants a monster spawned at run time,
which is M18's missile slots with a state machine attached. Carrying the
player from one level to the next is a front-end job -- `play.py` would have
to restart `pdftex` on the next map and hand the inventory in through
`\DTHOOK` -- and needs the engine only to print what the player is holding
when the exit fires.

### Weapon and status bar

Screen-space splices with no depth test, so they need nothing new beyond
`\dtSplice` and patch reading. Doom's `STBAR`, digits and face are ordinary
patches in the same picture format as sprites, so do them after that lands.

### Done: real flats, the sky, and packing the texel arrays

The loader was as easy as predicted. Two things were not.

**The renderer had a third option this section did not consider.** The choice
was posed as span-wise restructure versus per-pixel arithmetic in the column
loop. But along *one column* the world point is a scalar multiple of the
depth, because `lateral/depth = (PPLANE-x)/PPLANE` is fixed for the column:

```
world = (PX,PY) + depth * (cos - k sin, sin + k cos)     k = (PPLANE-x)/PPLANE
```

So the two direction terms are per-column constants (banks 4000/5000, rebuilt
once a frame) and each pixel costs one multiply-add per axis. Depth is
`planeheight * PPLANE / (y - HALFH)`, whose second factor depends on nothing
but the row, so `dtYS<y>` holds it for the whole run and **no pixel divides**.
Doom's own half-pixel bias is folded into that table, which is also what
removes the singularity at the horizon row. No span restructure, no departure
from the two-accumulator design.

Everything is 12.4 (sixteenths of a map unit). 16.16 overflows on the
near-horizon rows; whole units make the texture jump a texel as the camera
moves. The modulus that wraps a world coordinate into a 64x64 flat is a
rounding `\numexpr` and only floors correctly above zero, hence a 2^28 bias
(a whole number of 64-unit tiles, so it shifts nothing) and a cap on
planeheight.

**The real blocker was the string pool, and it changed the shape of the whole
project.** See §6. The texture array was 92% of the budget and flats simply
did not fit beside it, so `dtX` is now packed eight texels to a control
sequence and compositing runs column-major to build it. That is a bigger
change than flats were, it is pixel-identical to the old renderer under
`\DTNOFLAT`, and it is *faster* (§9).

`F_SKY1` is a marker, not a flat -- ten of MAP01's 59 sectors name it, and
drawing its 4096 bytes gives a ceiling of red rock stretching to infinity at
the horizon. It is drawn as a strip of a wall texture picked by view angle,
with no perspective and no shading, which makes it the cheapest span in the
renderer: two table lookups and a pick.

Not done: `\DTSKY` defaults to `SKY1` and the map number is not parsed, so
Doom II maps 12+ need it set by hand. Doom 1 IWADs are unverified.

### Done: real wall textures

`TEXTURE1`/`PNAMES` composition, sidedef textures and offsets, seg offsets,
Doom's pegging rules, `COLORMAP` shading on Doom's light table, and fake
contrast. §7 records the shape and why; the measurements are in §9. Walls no
longer shade by adding to a ramp base, so the palette-row constraint in §6
applies to planes only.

Three things are worth knowing if this is extended:

- **Texture names are matched case-insensitively.** Not a nicety -- see §6.
- **Widths are powers of two, heights are not.** MAP01 uses heights of 16, 64,
  72, 96 and 128, so nothing here may assume a mask; `u` and `v` both wrap by
  `\divide` against the texture's own dimension.
- **`\dt@vmod` is read inside a `\numexpr` that `\relax` terminates.** A bare
  `\dt@vmod` followed by a space produces no space token and the scan runs
  straight into whatever digits follow -- the §6 trap, from the other side.

### Done: sprite occlusion

It did look wrong -- see §7. `dtZT`/`dtZB` are the per-column span records.

---

## 9. Performance

Timings are machine-dependent by a factor of two or more; measure a baseline on
your own box before believing a regression. The machine these were taken on
runs the 320x200 benchmark at ~225 ms/frame, against ~180 ms/frame recorded on
the original development machine. The point is the ratio.

| what | cost |
|---|---|
| 200x120, built-in map | baseline |
| 320x200, built-in map | ~1.9x of 200x120 |
| collision, 601-seg map | ~12 ms/frame, 3 swept scans per move |
| things, 61 placed | small; the sort runs over visible things only |
| doors | negligible; 4 sectors ticked per frame |
| geometry only, no things | 0.87 of the billboard build |
| real sprites, 8 rotations | 1.03 of the billboard build |
| real wall textures, geometry only | 1.13x (213 -> 240 ms/frame at 320x200) |
| real wall textures, whole frame | ~1.0x; the sprite pass dominates |
| packing the texture array, geometry only | **0.84x** (341 -> 285 ms/frame) |
| packing the texture array, whole frame | **0.97x** (733 -> 713 ms/frame) |
| real flats + sky, geometry only | 1.62x (285 -> 461 ms/frame) |
| real flats + sky, whole frame | 1.26x (713 -> 897 ms/frame) |
| monsters, MAP01's 29, whole frame | **1.60x** (221 -> 354 ms/frame at 200x120) |
| the sprites their states need | 50 pictures -> 156; `\DTNOMON` keeps it at 58 |
| the sprites a map's missiles need | 156 pictures -> 172 on MAP01; `\DTNOMIS` keeps it at 156 |
| a masked middle's post chain | 2 more pictures read at load on MAP01, and a handful of drawsegs a frame |
| the noise flood | one queue over the sectors per SHOT, on an adjacency built once at load |
| per-pellet aim | a shotgun costs seven aim scans and seven sight scans against a pistol's one |

**Load costs are a different unit and belong in their own table.** They used
to be the last five rows of the one above with a paragraph wedged in between,
which meant they rendered as a second, headerless table and read as though
they were per-frame numbers. They are not: every one of them is paid once.

| what, once, at load | cost |
|---|---|
| the rest of the bar | 53 status-bar pictures -> 73, in the same single directory pass |
| `doom2.wad` MAP01 load | about 6000 `\pdffiledump` calls |
| COLORMAP + 26 pictures | +0.2 s, ~10000 macros |
| 40 composited textures, packed | +0.7 s, ~53k texel macros |
| 30 flats, packed | +0.4 s, ~15k texel macros |

The four measured rows there were taken in one sitting, best of three each, on
a box that runs the whole-frame benchmark at 733 ms against the 240 ms in the
frame table -- which is exactly why they are quoted as ratios and why you
should re-measure before believing any of them.

**M19, M20 and M21 are not in the frame table with numbers, and that is
deliberate.** None of them was measured against a baseline taken in the same
sitting, which is the only kind of measurement this section accepts. What can
be said without measuring is the shape: masked middles add work proportional
to the drawsegs a frame actually records (four linedefs on MAP01, so nothing);
the noise flood is per shot rather than per frame and is bounded by the sector
count once the adjacency exists; and per-pellet aim multiplies the aim scan by
the pellet count, which makes a shotgun blast seven times a pistol shot and
leaves the pistol -- what fires most of the time -- exactly where it was. If
you are about to change any of these, take the baseline first.

**Packing the texture array is free, and slightly better than free.** That is
not what I expected: it adds a macro call per wall pixel and it still came
out ahead, because 111k strings hash better than 477k. If you are weighing a
change that trades string count for per-pixel work, the trade is probably
worth taking.

Real flats cost 1.62x on the geometry pass and 1.26x on a whole frame. A
plane pixel needs its world position and a wall pixel does not, which is the
whole of it. Quote the geometry number when judging a change to the plane
path.

The texture numbers were taken against the pre-texture tree in the same
sitting, best of three each, because that is the only way any of this means
anything. The 1.13x on the geometry pass and the ~1.0x on a whole frame are
the same measurement seen twice: walls got 13% dearer and walls are not most
of a frame once sprites and the HUD are in it. Quote the geometry number when
judging a change to the wall path.

**Take every one of these in the same sitting.** The machine these were last
measured on ran 1.6x slower than it had an hour earlier, which is enough to
turn a 3% regression into a 50% one if the baseline is remembered rather than
re-measured.

### The harness was making the mistake this section warns about

`check.py`'s benchmark reported `secs / 10` for a ten-frame run -- the whole
run divided by the frame count, which is the one thing the paragraph above
forbids. It is fixed, and the size of the error is worth recording because it
is bigger than it looks. On the box below:

| | |
|---|---|
| ten frames at 320x200, built-in | 1.94 s |
| the same run at `\DTFRAMES{0}` | 0.40 s |
| **a frame, by difference** | **154 ms** |
| what dividing the whole run said | 194 ms, i.e. **+26%** |

And that is the *flattering* case, because the built-in map has no WAD to
parse. The benchmark now compiles both, best of three each and interleaved,
and reports the subtraction with the division beside it so the gap stays
visible. `\DTFRAMES{0}` is a real mode -- it loads everything and renders
nothing -- and nothing else in the harness exercised it.

### M22: the viewport, the pipe, and what they cost

All in one sitting, built-in map, best of three each, by difference over ten
frames (2 keys against 12), on the machine that gives 148 ms for the batch
320x200 row above:

| interactive mode | cost |
|---|---|
| 76x60, engine ASCII (`\DTASC{1}`) | 23 ms/frame |
| 76x60, `\DTPIPE` | **13 ms/frame** |
| 304x240, `\DTPIPE` -- the render alone | 167 ms/frame |
| 304x240, `\DTASC{4}` -> a 76x30 viewport | 217 ms/frame |
| 304x240, `\DTASC{1}` -> a 304x120 viewport | 374 ms/frame |

Two things fall out of that and neither was obvious before measuring.

**The pipe is cheaper than drawing ASCII**, by ten milliseconds at 76x60 and
by two hundred at 304x240. Handing the framebuffer to a front-end is one
`\write` per column of a macro that already exists; drawing it is a filter and
a string append per cell. So `\DTPIPE` does not trade speed for colour -- it
buys both, and `play.py` costs the engine nothing.

**`\DTASC{n}` is what makes a high-resolution interactive render affordable,
not merely legible.** The filter's per-pixel work is the same at every `n` --
every pixel is read and added once -- but the *appends* collapse: 76 per row
over 30 rows at `n=4` against 304 over 120 at `n=1`. The span accumulators are
O(current length) per append (see below), so that is a factor of sixteen off
the quadratic half. `\DTASC{4}` costs 50 ms of filter on top of the render;
`\DTASC{1}` at the same render costs 207 ms, more than the render itself.
A viewport smaller than the render is the cheap case, which is the opposite of
what you would guess.

**Measure a frame by DIFFERENCE, never by dividing a whole run.** The MAP01
figures above were taken as (24 frames − 0 frames) / 24 on the same tree in
the same minute. That is not fussiness: on the machine M15 was written on the
load alone is **82 seconds**, against the 3.5 s this file records elsewhere,
because MiKTeX's `\pdffiledump` appears to cost something proportional to the
size of the file rather than the size of the region -- and a map load is
about 6000 of them against a 15 MB IWAD. Dividing a whole run there reports
4 s/frame and hides a 1.6x regression completely. It also means **a `--quick`
harness run takes about 25 minutes on that machine and about two on a TeX
Live one**; if the suite feels impossibly slow, this is why, and it is
nothing to do with the engine.

Monsters cost 1.60x on a whole frame, and it is worth knowing where that
goes before optimising: the sight scan is the only new thing that walks all
601 segs, and it is already gated twice -- `A_Look` runs on a frame change
rather than every frame, and the shoot test runs behind a die roll. What is
*not* gated is that a woken monster walks toward the player and so ends up on
screen, which is sprite work rather than tick work. Separate those two before
believing either.

M16 costs nothing measurable per frame. `\dtCeilTick` loops over the ceiling
movers a map actually has and MAP01 has none; a staircase is built once, when
its trigger fires; and a light special is an assignment. The only per-frame
addition on a map with no crusher in it is a loop that runs zero times.

The dominant rendering cost is the `\xdef` string append per column per seg --
the span accumulators are O(current length) per append. The z-buffer version in
git history was about 2x faster but could not do portals. If this becomes the
bottleneck, accumulate a token register per column rather than a macro, or emit
several `\pdfliteral`s per column instead of concatenating.

Animation costs nothing per frame -- an `\ifcsname` that usually misses -- but
it roughly doubles the pictures a map loads, because every letter of a sequence
is a separate lump and a monster needs all of them at every rotation.  That is
where the cost of extending it will show up, not in the renderer.

One `\pdffiledump` per field is fine for map data. It is **not** fine for
sprite pixels: a post's run is one dump, and the picture reader would otherwise
make one call per pixel.

---

### What M14 cost

All of these in one sitting on the same single-core box, MAP01, 6 frames at
200x120, four runs each, against the pre-M14 tree in the same sitting:

| | mean |
|---|---|
| pre-M14 tree | 4.54 s |
| with pickups and the bar's numbers | 4.68 s |
| ...with `\dt@hudstate` stubbed | 4.47 s |

So the numbers and the face cost about **0.2 s over 6 frames, ~35 ms a
frame** -- 28 patch blits through the same `\dtBlit` the weapon already
used. The touch scan does not show up at all: it is 61 things against
collision's 601 segs on the same move, and it only runs when the player
actually moves.

Load is free, which is the part that needed care. 28 status-bar pictures
resolved in one directory pass: **3.68 s against the original tree's 3.57 s**,
i.e. inside the run-to-run noise. One `\dtLoadGfx` per picture would have been
27 more full scans of a 2928-entry directory at one file dump per entry --
roughly 79000 dumps against the 6000 the entire map load costs. See §8.

### What M23 cost

Nothing that can be measured in a frame. On the built-in map at 320x200,
interleaved and best of four in one sitting with the machine otherwise busy,
the engine as M22 left it took 225 ms a frame and M23's took 207 -- which is
noise, in the wrong direction. That is the expected answer: a map with no
sector specials loops zero times in `\dtSpecTick`, the shot scan is one
`\ifnum` when no line wants shooting, and the fixed colormap is one `\ifnum`
per wall column that is false unless a power is running. The costs that are
real are one-off and the player's own doing: a BFG going off is forty aim
scans and up to forty sight scans, and a teleport is a walk of the thing
list.

### Main memory, and which maps fit (M23)

Until M23 every measurement in this document was taken on MAP01, and MAP01
fits. The first fixture to load another map -- MAP09, for a masked middle of
two patches -- died half way through its sprites with `TeX capacity exceeded,
sorry [main memory size=5000000]`, and so did the engine as M22 left it. So
all thirty-two maps of `doom2.wad` were loaded and rendered for two frames
each, at 64x40, with the monsters awake:

| | words of main memory | strings |
|---|---|---|
| smallest: MAP32 | 2.7M | 76k |
| MAP01 | 3.4M | 121k |
| MAP09 | 8.1M | 220k |
| largest: MAP28 | 10.7M | 250k |

Every one of the thirty-two loads and draws with no errors and no warnings.
Thirteen of them stay under pdfTeX's default five million words; the rest
need more, and the most any needs is under eleven. The string pool, which §6
calls the binding resource, turns out not to be: packed eight texels to a
control sequence, the worst map uses half of it. **Main memory is the
resource that runs out**, because packing moved the cost there on purpose.

Two things about raising it are not obvious, and the second cost an hour.

- It can be raised at run time, without rebuilding the format: Web2C reads
  `extra_mem_top` from the environment, and MiKTeX takes
  `--extra-mem-top=N`. `play.py` and `check.py` do both -- the variable
  always, the option only when `pdftex --version` says MiKTeX, because TeX
  Live refuses an option it does not know. Only the MiKTeX half has been
  run; the TeX Live half is Web2C's documented behaviour and nothing more.
- **It has to be the TOP.** TeX's memory is one array with variable-size
  nodes -- boxes, glue -- growing up from the bottom and one-word nodes
  growing down from the top, and a token list is one-word nodes. This
  engine is nothing but token lists. `extra_mem_bot` adds its room below the
  variable-size region, where token lists cannot reach it: a load that
  overflows at five million words overflows identically at forty-five. And
  the statistics line then counts the unused forty million as used, so
  `\tracingstats` reports "41370046 words of memory out of 45000000" for a
  load that needed a million and a half. Believing that number sent the
  search into the sprite loader for a picture that did not exist.

MiKTeX grants about 13.2 million words however many are asked for, which is
enough for every map here and would not be for a PWAD much bigger than
MAP28.

## 10. LaTeX

`doom.tex` runs unmodified under `pdflatex`, with no packages, and the output
is **pixel-identical** to `pdftex` (verified on the built-in map and on
`doom2.wad` MAP01, with sprites on):

```latex
\documentclass{article}
\begin{document}
\def\DTWAD{doom2.wad}\def\DTMAP{MAP01}
\input doom.tex
\end{document}
```

`check.py` verifies this on every run, against `doom2.wad` MAP01 with sprites
and the HUD on. **Do not let that check lapse**: it is the only one that runs a
second format, and the bug it caught -- a hand-picked register block colliding
with LaTeX's allocator -- produced a clean compile, a plausible walk, and a
camera that sank through the floor.

This works because the file ends with `\csname @@end\endcsname\end`: under
LaTeX, `\@@end` is the primitive `\end` and terminates the job there; under
plain TeX it is undefined, `\csname` makes it `\relax`, and the following
`\end` is the primitive. Keep that line exactly as it is.

---

## 11. Working notes

- Rebuild and check in one step: `python3 check.py`.
- To inspect a frame, decode the PDF rather than rendering it -- `dump.py`
  prints one as ASCII. `--ramp` keys on the palette ramp, i.e. on *which
  surface* produced each pixel, which is far more diagnostic than the image.
  For a whole recording rather than one frame, `gif.py`. Neither needs a
  `--wad` any more: the palette is in the PDF.
- **To iterate on a mutation, do not run the whole suite.** `mutate.py` is
  importable now, and `mutate.apply(name, dir)` builds a mutant tree without
  running anything in it -- so a scratch script can drive one mutation
  against just the section of `check.py` it is supposed to redden. On this
  machine that is the difference between a minute and half an hour, per
  attempt. Copy the tree without `doom2.wad` while iterating and the IWAD
  fixtures skip themselves.
- **`play.py --record` and `--replay --verify` are a regression test you can
  take by hand.** The engine is deterministic, so a recorded key stream plus
  its per-frame state is an assertion about every frame of a run. If you are
  changing something whose effect you cannot name in advance, record a demo
  before and replay it after: the first frame that diverges is the answer.
- **To prove a rendering feature does anything, build a second copy of the tree
  with that feature stubbed out and diff the decoded frames.** That is how the
  sprite pass and the depth test were each shown to be load bearing, and it
  takes about a minute.
- `\dtTexRow{NAME}{v}` prints one row of a composited texture as hex, in a
  single `\message` so it survives `max_print_line` wrapping. `check.py`
  composites the same texture by its own route and diffs. It is the only
  handle on the composite itself rather than on how the composite looks.
  It samples through `\dt@usetex`, not by splitting `u` itself -- see the
  mutation table below for why that distinction is the whole value of it.
- `\dtFlatRow{NAME}{v}` does the same for a flat. Its probes must be
  **asymmetric**, and `check.py` asserts that rather than assuming it: `dtFL`
  is indexed `[row][column]` because Doom's `R_DrawSpan` reads
  `source[(y&63)*64 + (x&63)]`, and swapping the pair mirrors every floor in
  the map about its diagonal -- invisible on a symmetric tile, and invisible
  to a probe chosen from one.
- **A check that fires in only one direction still guards its branch; one
  written in the wrong direction guards nothing and looks the same.** Forcing
  `DONTPEGTOP` on every linedef of MAP01 changes not one pixel -- every upper
  visible on the scripted walk already has it, and a zero-height upper's two
  formulas differ by exactly the texture height, which wraps to zero.
  *Clearing* it moves 7640. Check the polarity by experiment before believing
  a green result.
- **When you add a check, break the code on purpose and confirm it fails.** The
  palette check passed the moment it was written and only proved worth having
  after reintroducing the bug to watch it go red. This is now mechanised:
  `mutate.py` copies the tree, applies one deliberate bug, runs
  `check.py --quick`, and prints what went red. Seventeen mutations, eight
  covering M12 and the packing:

  | mutation | goes red |
  |---|---|
  | flat rows read one byte late | both flat composite probes |
  | flats never chosen | 4, incl. artwork and both camera checks |
  | camera x dropped from the sample origin | the x check only, y stays green |
  | plane distance falloff removed | the distance check |
  | `F_SKY1` drawn as an ordinary flat | all 4 sky checks |
  | sky ignores the facing | turning moves the sky |
  | sky row scale wrong | every sky column is a strip of SKY1 |
  | texture pick stuck at 0 | all 3 composite probes |

  and nine covering M13, the sector machinery:

  | mutation | goes red |
  |---|---|
  | tag matches every sector | 3, none of them the tag control -- see below |
  | tag acts on the first match only | every-sector-with-that-tag |
  | once-only line never spent | the refire check |
  | switch face never swapped | the switch check |
  | crossing never fires | the walkover pair |
  | camera never rides a moving floor | the eye check, alone |
  | lift target is its own floor | the lift and the eye |
  | linedef tag read two bytes late | 5, across tags, switches and lifts |
  | fires every mover, not the tagged ones | the tag control |

  and ten covering M15, enemies and combat:

  | mutation | goes red |
  |---|---|
  | sight sees through walls | the independent-sight comparison |
  | sight blocked always | the same, from the other side, plus every wake |
  | monsters never wake | wakes, closes, shoots, and the damage steps |
  | damage never lands | shoots and the steps, with the wake still green |
  | armour absorbs nothing | the armour rule alone |
  | aim ignores the miss | the decoy check -- see below |
  | kills never counted | the kill count and "not killed twice" |
  | death never settles | the corpse check |
  | corpses still block | the block check, via its control |
  | ammo never spent | the bullet count |

  seven covering M16, the sector types:

  | mutation | goes red |
  |---|---|
  | crusher never returns | "goes back up, and comes down again" |
  | crusher does no damage | the crush pair |
  | crusher stop ignored | the stop pair |
  | stairs do not accumulate | 8/16/24 becomes 8/8/8 |
  | stairs ignore the flat | the chain runs away past its own map |
  | light always full | "reads the neighbour" |
  | exit does not end the level | the exit pair |

  and three covering M17, the rest of the bar:

  | mutation | goes red |
  |---|---|
  | arms never lit | "lights the weapons you are carrying" |
  | ammunition box shows the maximum | the held column, and the backpack pair |
  | keys never shown | the key that is being held |

  These twenty have not yet been run one at a time -- `mutate.py` copies the
  tree and runs the whole harness per mutation, and on the machine they were
  written on that is twenty-five minutes each. `python3 mutate.py --verify`
  says every one of the 47 still finds exactly its target, but **"applies" is
  not "reddens something"**, and until each has been watched it is a mutation
  with an unknown subject. That is their honest state; §11's whole argument
  says to write that down rather than imply otherwise.

- **`mutate.py --verify` is new, and it exists because this milestone broke a
  mutation and nothing noticed.** M16 rewrote `\dt@tagdo` from an `\ifnum`
  to an `\ifcase`, and M13's `fires-every-mover` -- the one mutation written
  specifically to redden a control nothing else could -- quietly stopped
  applying. `MUTATION DID NOT APPLY` prints as one line at the top of a
  half-hour run and then nothing goes red, which at a glance is
  indistinguishable from a mutation that ran and found nothing. The verifier
  costs no compiles and also flags an `old` string that matches more than
  once, since `replace(..., 1)` would silently take the wrong one.
  **Run it after moving any line a mutation names**, which in practice means
  after moving any line.

  **`aim-ignores-the-miss` is the one that taught something**, and it is
  §11's standing warning again. Written against the first firing fixture it
  reddened nothing: the monster was dead ahead, so a scan that had stopped
  caring how far off the ray a thing is still picked the same target. The
  answer was not to drop the mutation but to build the fixture it was
  waiting for -- a **decoy** at (200,400), *nearer* along the facing than
  the target at (300,256) and 144 units off it. Nearer is the whole of it: a
  decoy placed beside the target at the same distance leaves the bug
  invisible, because the scan takes the first candidate at equal range and
  that is still the target. Ask what shape of bug a check is waiting for,
  then go and build it.

- **Auditing which checks have been *watched* to fail is a separate job from
  writing mutations, and it found a hole here.** After eight M13 mutations the
  control "moves no sector carrying a different one" had never once gone red.
  The reason is specific and would not have been guessed: MAP01's switch 84
  lowers to the *highest neighbouring floor*, and for the lift's sector that
  target is its own floor, so a lift mis-marked by that line sits still and
  looks innocent. No marking-time bug can expose the control. Only a
  fire-time one can -- `fires-every-mover` sends the lift's own recorded
  target of 40 into a sector tagged 3 -- and it was written for exactly that
  reason. If a check will not go red, the honest question is not whether it is
  fine but *what shape of bug it is waiting for*.
- Three M13 checks remain unreddened, and they are unreddened for structural
  reasons rather than by omission. Two are controls -- "the same walk with the
  special removed never gets through" and "with the switch unpressed the floor
  never moves" -- whose job is to fail when the positive check would pass
  vacuously, so a mutation that breaks the feature is precisely the case that
  leaves them green. The third, "the pressed face is a real composited
  texture", is an assertion about the WAD's data rather than about this
  engine's polarity: no code mutation can falsify it, because `SW2BRCOM` is in
  `TEXTURE1` no matter what this code does. It earns its place by catching a
  *different* WAD, not a different bug.

  **Three of the eight found holes in checks that had passed since the day
  they were written**, and each hole was of a different kind:

  - *Measuring the wrong surface.* "Planes draw real artwork" scanned every
    pixel in the frame, and wall textures supply rows outside the ramps by
    themselves -- so it stayed green with the flat path disabled entirely.
    It needed a fixture that forces the walls procedural (`\dt@txok` false)
    so that only the planes can produce the evidence, plus a `\DTNOFLAT`
    control.
  - *Counting instead of forcing.* "Plane shading spans many colormap
    levels" sounds like a statement about distance shading and is not: 30
    flats across 59 sector lights reach 105 colours with the depth term
    deleted. Replaced by forcing the falloff constant to zero and requiring
    the picture to notice -- which is why that constant lives in `\dt@plit`
    rather than inline, on the `\DOORRAMP` precedent.
  - *Checking storage and calling it coverage.* `\dtTexRow` split `u` itself,
    so it verified the packed array and left the half of the packing that
    runs per column untested: a `\dt@usetex` that always took the first of
    eight draws every wall as eight-fold vertical stripes and passed the
    entire harness. The probe now goes through `\dt@usetex`.

  A check that has never been watched to fail is a check with an unknown
  polarity and an unknown subject. Budget an hour per feature for this; it
  found more real problems per minute than anything else in the project.
- **When a check fails, suspect the check.** The door-collision check failed
  because it assumed the player started next to the door; they start 284 units
  away and arrive exactly as the opening clears 56. The engine was right.
- **A stub that changes nothing has two explanations, and "the code is inert"
  is the less likely one.** Deleting the sidedef row offset from `\dtSegDraw`
  changed not one pixel of any fixture, which reads exactly like dead code.
  It is not: MAP01 has 38 sidedefs carrying a row offset and all of them
  happen to lie off the scripted walk. *Forcing* a value settled it in one
  compile where stubbing could not -- 84k pixels moved. Prefer forcing to
  stubbing whenever a stub comes back zero, and remember that the same shape
  of mistake produced the §8 bisection that contradicted itself for months.
- Choose probe colours that the map does not use. Two sprite measurements were
  wasted on ramp 4 and ramp 7, which are also the built-in map's wall and floor
  ramps. Rows 16, 48 and 208 are unused by the built-in level.
- **Do not edit the tree while the harness is running in it**, and on a slow
  machine that advice needs saying twice, because a `--quick` run there is
  twenty-five minutes and the temptation to keep working is exactly
  proportional. Editing `doom.tex` while pdfTeX was part way through reading
  it produced `! Extra \fi` on the line that closes the WAD load -- an error
  in a file that was, by then, perfectly balanced. Ten minutes went into
  hunting a conditional that was never wrong. Keep a second copy of the tree
  for iteration and drive only the fixtures under test; the built-in-map ones
  cost a second and a half each. `.md` files are safe to edit mid-run --
  nothing compiles them. (Since M23 the harness compiles a COPY of the engine
  that it takes into `build/` as it starts, so this particular accident can
  no longer happen; the advice about iterating in a second tree stands.)
- Keep a known-good copy before large changes.
- pdfTeX errors are terse. `-interaction=nonstopmode` plus
  `grep -A6 '^!' log` gives the expansion context, which usually names the
  culprit.

### M14's mutations, and what they found

Ten new entries in `mutate.py`, run one at a time against the full `--quick`
harness. Every one of them reddened checks, and after two corrections every
one reddens a check that *names the thing it broke*:

| mutation | red |
|---|---|
| `pickup-never-spent` | 3, incl. the thing that is collected forever and never disappears |
| `pickup-ignores-refusal` | the medikit that vanishes having healed nothing, and MAP01's third medikit |
| `pickup-height-ignored` | the ledge check, alone -- nothing else in the suite can see it |
| `weapon-never-switches` | the give rule and the real-map shotgun walk |
| `ammo-always-bullets` | the bar's count and the shotgun walk |
| `digit-pitch-per-digit` | the layout check: "one more digit starts 11 columns left, STTNUM0 is 14 wide" |
| `digits-march-right` | 4, incl. right-alignment: "11 and 111 both end at column -1" |
| `face-pain-rounds` | the pain table and the truncation check: `STFST10` where `STFST00` belongs, at exactly 80 health |
| `armour-always-given` | the armour rule |
| `backpack-doubles-twice` | "200 -> 400 -> 800" |

**Two of my own checks were passing for the wrong reason, and only the
mutations found it.** Both are the same failure of imagination -- assuming a
measurement window contains only the thing it is named after -- and both are
worth reading before adding a pixel check of any kind.

*The thing that was already behind the camera.* "The stimpack is gone from the
map" was written on a walking fixture and passed. It could not have failed: by
the time a forward walk is close enough to collect something, the next step
puts that something behind the view, so **zero pixels of it** is what a
working engine, a broken one, and an empty room all report alike. The fix was
to stop walking -- one 1-unit step in `\\DTHOOK` drives the touch scan through
the ordinary movement path, then `\\dtStepFwd` is stubbed and every frame looks
at the spot where the thing stood. It now reads 4220 pixels with the scan
stubbed against 0 with it live.

*The percent sign, and then the neighbouring widget.* The health box was
x 46..91, and `digits-march-right` did not redden "health digits are
right-aligned" -- because column 90 is where `STTPRCNT` starts, so the percent
sign alone kept the check satisfied while the digits marched out of the field
entirely. Narrowing to 90 fixed that and exposed a second layer:
`digit-pitch-per-digit` was reddening the *pitch* check for the wrong reason,
reporting a nonsensical "0 columns left". At a pitch narrower than the glyphs,
the AMMO number overruns its own field as far as column 47 and lands in the
health box. Two changes settled it: the box became 48..90, and the two
alignment fixtures now hold **the fist**, which draws no ammunition count at
all, so the only digits anywhere near the health field are the health field's
own. The detail line then reads what it should: 11 columns against Doom's 14.

The general lesson, third time of asking: **a pixel check needs a window that
cannot contain anything but its subject, and the way to find out whether it
does is to move the subject and watch.** A control render is not enough on its
own -- `hudoff` was a perfectly exact control, and the check still measured
the wrong widget.

Three failure details also read as passes when the check failed (`"and gone"`,
`"weapon 1 -> 2"` and `"second refused"` were static text next to computed
values). They are now all computed. A detail line that cannot contradict its
own label is worse than none.

### M18's mutations, and what they found

Nine new entries, all run against the built-in half of the harness (the
projectile checks are all built-in fixtures, so the IWAD section adds nothing
but half an hour). Seven reddened checks that name the thing they broke:

| mutation | red |
|---|---|
| `missile-never-stopped` | 7, headed by "exploded at x=1231 (east wall 1024), 0 explosions" |
| `missile-ignores-its-height` | the step and the ceiling, alone -- exactly the pair the z fixtures exist for |
| `missile-z-per-substep` | the fireball that arrives: "exploded at z=0 after falling through 14 heights" |
| `blast-does-not-fall-off` | "100 points at the near monster against 100 at one forty units further" |
| `missiles-never-recycled` | "24 slots for 24 explosions" against 49 |
| `monster-never-throws` | the imp, both halves |
| `missile-never-drawn` | "0 px differ from the same walk with nothing thrown" |

**Two reddened nothing, and both are worth keeping written down.**

*`missile-does-no-damage` -- and this one was a real gap.* Deleting
`\dtHurtThing` from `\dt@misboom` outright left every projectile check green,
because "a rocket kills what it hits" was watching the **blast**: 128 points
of `P_RadiusAttack` kill a 20-health zombieman on their own, so the direct
hit could be removed and the monster still died on schedule. It is §11's
lesson in the damage numbers rather than in the pixels -- *a measurement
window that contains more than its subject*, where the window happens to be
"what killed this monster". The fix is a plasma bolt, which carries no splash
at all: what kills the zombieman there can only be the impact, and the same
fixture asserts the player a hundred units away is untouched, which is the
control that says the blast really is absent.

*`missile-forgets-its-owner` reddens nothing, and the reason is structural.*
`\dtMSELF` is not what keeps a missile off its launcher here -- the spawn
clearance is. `\dt@misclear` starts a missile a shooter's radius plus its own
plus four out along the bearing, so it is never inside the thing that threw
it, and a monster in its attack state does not move afterwards to catch up
with it. `\dtMSELF` is still set, because it is correct and costs one
assignment, and because anything that later lets a missile be spawned into a
crowd or lets its owner keep moving will need it. But no check guards it
today, and a check that claimed to would be lying.

### M19, M20 and M21's mutations, and what they found

Twenty-one more entries. Sixteen reddened a check that names the thing they
broke on the first run; the other five are the interesting half, and four of
them found a check of mine that was passing for the wrong reason.

| mutation | red |
|---|---|
| `masked-middle-never-recorded` | all three: nothing is drawn at all |
| `masked-middle-ignores-pegging` | all three -- the railing goes to the top of a 264-unit opening and off the screen |
| `masked-middle-one-column` | the transparency check, alone: the bars are still there and still the right height, and are no longer bars |
| `locks-are-not-checked` | "stayed in sector(s) [0, 1, 2] through 0 refusals" |
| `locked-refusal-spends-the-line` | the once-only line, after being repointed -- see below |
| `no-retaliation` | both halves of the infight |
| `attacks-always-hit-the-player` | the imp's own health, alone |
| `missiles-hit-their-own-kind` | the species rule, after a check was written for it |
| `gunshots-are-silent` | the woken monster |
| `sound-goes-through-doors` | the shut door, alone -- the other two stay green, which is the point |
| `weapons-have-no-cooldown` | "40 pistol shots and 40 shotgun in 40 frames" |
| `cooldown-never-expires` | "1 pistol shots and 1 shotgun" |
| `pellets-share-one-aim` | the second monster, after the fixture was fixed |
| `no-shot-is-ever-accurate` | the accurate first shot, after the fixture was fixed twice |
| `sight-ignores-height` | the monster that wakes over a parapet |

**Four of my own checks were passing for the wrong reason.** All four are the
same failure and it is §11's from the top of this file, in four new costumes:
*a measurement window that contains more than its subject*.

*The target that died mid-blast.* "A shotgun blast lands on more than one
thing" put two 20-health zombiemen side by side, and both ended up hurt with
the spread deleted -- because the first of them **dies part way through the
blast**, stops being a target, and the remaining pellets go to the second on
their own. Five hundred health apiece is the fix: a target that cannot die
inside one blast is the only arrangement in which the second one being hit
means what it says.

*The shot that hits one thing either way.* "The first shot of a pistol burst
is not spread" asserted that exactly one thing was hit -- which a single
pellet does whether it is aimed or scattered. It needs a target the accurate
ray **cannot** reach, and then it needs two more corrections: at 29 units off
the facing the spread had four degrees to find and the die rolled less than
one, and at 22 units on the left the die leaned right. **A single
deterministic draw has a sign as well as a size**, and a check that depends
on the sign is a coin toss written down as an assertion. One target either
side settles both.

*The species rule nothing was watching.* `missiles-hit-their-own-kind`
reddened nothing because the infight fixture is an imp and a zombieman, and
the rule is about two things of one type. A second imp in place of the
zombieman is the check, and it is a good one: four fireballs thrown straight
past it, both still on full health.

*The mutation that went red in the wrong place.* `locked-refusal-spends-the-
line` originally swapped `\dt@keytest` and `\dt@fireact`, which made every
locked door open and reddened "refuses a player without its key" instead of
the check it was named for. **A mutation that goes red somewhere is not the
same as a mutation that goes red where it meant to**, and the run reads
identically either way unless you check which line went. It now spends the
line on every press and leaves the refusal working, which reddens the
once-only check alone.

**And one reddens nothing, for a reason worth keeping.**
`masked-middle-ignores-the-clip` deletes `\dt@thingclip` from the masked pass
and changes not one pixel: the railing the fixture stands in front of has
nothing between it and the camera, so there is no silhouette to clip
against. Guarding it needs a fixture with something standing in front of the
bars, which is a thing to build the next time this code is touched rather
than a line to delete now.

### M22's mutations, and what they found

Six, all applying and all red on the first attempt, which is unusual here and
is mostly a sign that this milestone touched less of the engine than the ones
before it. Run against the **whole** non-IWAD suite -- 157 checks, baseline 0
red -- and not merely against M22's own block, because a runner that only
watches the checks a mutation was aimed at cannot see it reddening something
else, which is a failure mode this section already records twice.

| mutation | red | which |
|---|---|---|
| `viewport-shrinks-the-render` | 3 | leaves the render alone, + 2 consequential |
| `viewport-never-averages` | 1 | a cell is the mean of its block |
| `viewport-samples-one-row` | 1 | ditto |
| `luma-ignores-green` | 1 | ditto |
| `pipe-sends-one-column` | 1 | the pipe hands over what the PDF gets |
| `gif-widens-a-code-early` | 2 | both GIF decode checks |

Nothing outside the family each mutation belongs to went red, which is the
result you want and is not the one you always get.

**The two consequential reds are worth naming rather than counting.**
`viewport-shrinks-the-render` makes the render 76x30, and at that size the
`\DTASC{2}` viewport is seven rows -- coarse enough that the box mean and the
point sample agree on all seven, so the control check "and a mean, not a
sample" goes red having detected nothing. It is a knock-on, not a second
independent catch, and reading the count alone would overstate what that
mutation proves.

`gif-widens-a-code-early` reddens its two with `0 frames at 0x0`: the decoder
does not produce wrong pixels, it refuses the file. That is the signature of
a genuinely independent decoder and is the whole point of the mutation.

`viewport-shrinks-the-render` is the one worth understanding, because it is
the implementation a reasonable person reaches for first. `\DTASC{4}` could be
made to look right by rendering at 76x30 and enlarging the page, and the game
would play *identically* -- same keys, same walk, same terminal, pixel for
pixel. Every other check in this file about the viewport is a check on what
the terminal shows, so not one of them can tell the two apart. The check that
can is the one that renders the same walk twice at two decimations and
compares the PDFs, and it exists solely for this.

`gif-widens-a-code-early` is not a `.tex` mutation, deliberately. It restores
a bug `gif.py` actually had: LZW codes widening one code before the decoder
widens them. What makes it worth a permanent row is how it was found. A
round-trip against a decoder written alongside the encoder passed every frame
-- both ends shared the misconception -- and the file was only refused when
something foreign tried to read it. `check.py`'s `ungif()` is therefore
written from the *decoder's* side of the spec, and this mutation is the
standing check that it still is. If it ever reddens nothing, that
independence has lapsed and the round-trip has become a mirror.

### Two traps this milestone added to the pile

**A module worth importing needs a `__main__` guard, and `mutate.py` had
none.** Everything below `MUTS` ran at import, so `import mutate` -- the
obvious way to read the table, and the obvious way to drive one mutation
against a subset of the checks -- started a full pass instead: 78 mutations,
a whole `check.py` each, with no output at all until the first finished. It
looks exactly like a hung import. `mutate.py` now has the guard and an
`apply(name, dir)` that builds a mutant tree without running anything in it,
which is what makes iterating on a new mutation take a minute rather than an
afternoon.

**`\read16` prints its prompt with no newline, so the next line arrives glued
to it.** `\dt@key=` lands in front of the first viewport row of every frame
but the first. Anything reading the terminal has to strip it -- `check.py`'s
`asciirows()` did not, and quietly returned 29 rows a frame where there were
30, which reads as a working viewport and would have made any count-based
check on it a lie. It is §6's "an error prompt eats a line of stdin" seen
from the reading end, and the fix is the same shape: the reader has to know
the prompt is there.

### M23's mutations, and what they found

Forty-seven new entries, which takes `mutate.py --verify` to 125. Forty of
them are about the built-in map and were each run against M23's own section
of the harness rather than the whole of it -- sixty-two checks, two minutes,
eight at a time -- which is what `mutate.apply()` exists for. All forty went
red, and went red where they were aimed. Three things are worth keeping.

The other seven need a real map -- the fixed colormap's four, the masked
builder's and the loader's two -- and were run against the IWAD half of
M23's checks alone, beside an unmutated tree that had to come back clean. It
did, and all seven went red: each of the four `fixed-colormap-skips-...`
mutations
on "invulnerability draws the whole view through the inverse map", which is
the point of making that check a statement about every pixel -- walls,
planes, the weapon and the sprites in shot each have to be right for it to
pass, and leaving any one of the four out is enough to fail it.

Running those six the obvious way first -- a whole `check.py` each, six at
once, beside a sweep of all thirty-two maps -- took over an hour without
finishing and was killed by its own timeout. `mutate.py` on its own does
exactly that, one after another; on a machine with a slow `\pdffiledump`,
budget a night for it or drive the section that matters.

**Every one of them going red is less reassuring than it sounds, because the
checks were written the same week by the same hand.** The two that earned
their keep were the ones written to be awkward. `teleport-ignores-the-tag`
only reddens because the fixture carries a decoy destination, earlier in the
thing list and in the wrong sector -- on a map with one teleporter "the
first destination found" is the right answer. And
`raise-takes-the-first-floor-up` only reddens because the sector that moves
is dropped to -40 first, giving it two higher neighbours to choose between.
Both fixtures were built that way before the mutation was written; a
mutation that can only fail against a fixture designed around it is a
description of the fixture, and the fixture is the part that matters.

**`tracer-turns-on-a-sixpence` had no check to redden until it was
written.** A missile that turns as hard as it likes still arrives, so "it
finds the player" passes. The check that sees it asserts the turn itself: no
frame may change the missile's heading by more than 240 units, and at least
one frame must use all of them. That second half is what makes the first
mean anything -- a missile that never needed to turn hard would satisfy a
bare upper bound.

**`played-level-never-ends` crashed the harness instead of failing it.**
With the exit ignored the key loop reads past the end of its input, pdfTeX
dies before the first page, and `frames()` raised on a PDF that was never
written. A check that throws is not a check that fails: everything after it
in the run is lost. `frames()` returns no frames for no file now, which is
what it always meant.

### Three traps M23 added to the pile

**`re.findall` returns bare strings when the pattern has one group.**
`dumpfix()` zips field names against each match, and with a single field the
match is a string, so the name was zipped against its CHARACTERS: `4960`
read as `4`, `25` as `2`, `9973` as `9`. Six checks failed at once, on six
different features, and five of the six "failures" were the engine being
right. What gave it away was that every wrong number was the leading digit
of the right one. The general shape is this section's oldest: a helper that
is wrong makes every check that uses it wrong together, and that looks like
the engine.

**A dump taken after the first tick has already lost the first step.** The
turbo floor climbs sixteen a frame for one frame and eight for the next, and
the per-frame dump -- written from a wrapper round `\dtPlayerTick`, which
runs after `\dtSectorTick` -- first reads the floor after that sixteen has
happened. Differences between consecutive readings were `{8, 0}` and the
check said the floor was not fast. The starting value has to be put back on
the front of the series by hand, and it is a number the fixture chose.

**An off switch has to cover the fixture's own call.** `\DTNOSPEC` first
guarded the engine's call to `\dtSpawnSpecials`, and every fixture that
gives the built-in map a special calls that macro again from `\DTHOOK` --
so under the off switch the secrets were counted anyway. The guard is inside
the macro now. It is the `\DTUSE`-before-`\DTHOOK` trap of §6 from the other
direction: a setting read in one place does not bind code reached from
another.

### One more, found while recording the Doom II clip

**The palette section stays open until the first frame, and the first tick
talks inside it.** `[DTPAL]` goes out once, before the loop starts, and
nothing closes it but the first `[DTS]` -- so whatever the engine says
during the first tick lands between the two. `play.py` took every
hex-looking fragment of that as more palette: a hook that logged monster
positions put a handful of stray digits on the end, the string came out an
odd length, and `bytes.fromhex` refused it before a single frame was drawn.
A secret sector under the player start would have done the same. The
palette is now the lines that are hex and nothing else, cut to 256 entries,
and the rest is passed on as what it is: the engine talking. One check
(`\message{abc}` on every tick -- three hex digits, the worst case) and one
mutation, `palette-takes-every-hex-digit`, which makes 126.
