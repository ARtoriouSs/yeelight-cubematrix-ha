# Yeelight Cube Matrix for Home Assistant

Play pixel art and animations on a Yeelight Cube Matrix lamp. The integration talks to the lamp over your local network. There is no cloud.

You get:

- a **light** (on/off and brightness),
- a **select** called Background (what the cube shows when nothing else plays),
- a **sensor** called Playing (the name of what is on the cube now),
- actions to play pixel art, animations and generated effects.

## Supported hardware

- Yeelight Cube Matrix (model `cubematrix`). The lamp must be a Cube Matrix, other Yeelight lamps are refused.
- One or more 5x5 matrix modules, with optional spot modules, chained in a row or in a column.
- Several cubes in one Home Assistant are fine, each one is its own entry.

Tested with Home Assistant 2026.10 (Python 3.14).

## Before you install

### Turn on LAN Control

In the Yeelight app, open the lamp, go to its settings and turn on **LAN Control**. Without it the lamp does not answer on the network.

### Reserve the lamp IP in your router

The integration finds the lamp by its IP address. Give each lamp a fixed address:

1. Find the lamp's MAC address in your router's device list.
2. In the router's DHCP settings, add a reservation (static lease) for that MAC and the IP you want.
3. Restart the lamp, or wait until it renews its lease.

This way the lamp keeps the same address after a restart of the lamp or the router.

## Installation

### HACS (custom repository)

Not published yet. When it is:

1. In HACS, open the menu, choose **Custom repositories**.
2. Add the repository URL, category **Integration**.
3. Install **Yeelight Cube Matrix** and restart Home Assistant.

### Manual

1. Copy the folder `custom_components/cubematrix` into the `custom_components` folder of your Home Assistant config folder.
2. Restart Home Assistant.

## Setup

1. Go to **Settings > Devices & services > Add integration** and pick **Yeelight Cube Matrix**.
2. Enter the lamp's IP address.

The integration checks that the lamp answers and that it is a Cube Matrix. A lamp can be added only once.

### Options

Open the integration and choose **Configure**.

| Option | What it does |
| --- | --- |
| Module layout: `modules` | The modules in chain order, split by commas, for example `matrix, spot, matrix`. Default: `matrix`. At least one `matrix` is needed. |
| Module layout: `direction` | `horizontal` (modules side by side, canvas 5 pixels high) or `vertical` (modules stacked, canvas 5 pixels wide). Default: `horizontal`. The first matrix is the leftmost one, or the bottom one when vertical. |
| Add a preset (`name`, `text`) | Save a pixel art in the pixel art format. Shared by all cubes. |
| Edit a preset (`name`, `text`) | Change a saved preset. |
| Delete a preset (`name`) | Remove a saved preset. |

The canvas size follows the layout: two matrices side by side are 10x5, three are 15x5, two stacked are 5x10.

## Connection

The integration talks to the lamp in two ways:

- **LAN control** on TCP port **55443**: setup, power on and off, and messages from the lamp. Normal LAN control allows only about 60 commands per minute, which is about 1 frame per second.
- A **UDP session** on port **55444** for the frames. The integration opens the session, puts the lamp in direct mode inside it, and then sends frames without waiting for answers. A keep-alive goes out every 10 seconds.

Only outgoing connections are used, so no firewall port has to be opened on the Home Assistant host. If Home Assistant runs in a container, normal outgoing network access is enough.

What to expect:

- **Frame rate**: at most 25 frames per second. Smooth animation up to 40 fps was seen, but the lamp answers only about 30 messages per second on UDP, and keep-alives and commands share that. Higher `fps` values are cut to this limit.
- **Short blinks**: now and then the lamp leaves direct mode by itself and answers frames with "illegal request". The integration puts direct mode back at once, which shows as a very short blink. This is normal and is only logged at debug level.
- **Frame size**: the lamp drops UDP messages over about 760 bytes. This is enough for about 6 matrices. With a bigger layout the integration logs a warning and uses LAN control (about 1 frame per second).
- **Fallback**: if the UDP session doesn't work, the integration logs one warning and sends frames over LAN control at about 1 frame per second. While the background plays, UDP is tried again every 5 minutes.
- **LED order**: inside a matrix, LED 0 is at the bottom right, the LEDs go to the left along the row, and the rows go from the bottom up. The integration takes care of this, art is always written top-left first.

## Pixel art format

An art is a YAML mapping. This is a small arrow:

```yaml
name: arrow_demo
size: 5x5
palette:
  x: '#00b4ff'
fps: 8
frames:
  - |
    ..x..
    .xxx.
    x.x.x
    ..x..
    ..x..
```

| Key | Meaning |
| --- | --- |
| `name` | Name of the art. Needed in preset files and saved presets. |
| `size` | `WIDTHxHEIGHT`, for example `5x5` or `10x5`. |
| `palette` | Maps one character to a colour. `.` is always off (black). |
| `frames` | A list of frames, at least one. One frame means a still picture. |
| `fps` | Frames per second. Default: 5. |
| `spots` | Colours for the spot modules (see below). |
| `loop` | Accepted for compatibility, but not used. Use `repeat` in the `show` action. |

Frames are written **top-left first, row by row**, as you see the cube. Whitespace inside a row is ignored, so `. . x . .` works too.

Colours are `'#rrggbb'`, `off` (with or without quotes, both work), or `[r, g, b]` with values from 0 to 255.

### Raw arrays

Instead of a palette, a row can be a list of colours:

```yaml
name: red_dot
size: 2x1
frames:
  - - [[255, 0, 0], "off"]
```

A frame can also be a list of rows, each row a text or a list. Text rows and list rows can be mixed.

### Per-frame duration

Give a frame as a mapping with `pixels` and `duration` (seconds) to override `fps` for that frame:

```yaml
name: blink
size: 5x5
palette: {x: '#ffffff'}
frames:
  - pixels: |
      .....
      .....
      ..x..
      .....
      .....
    duration: 2
  - pixels: |
      .....
      .xxx.
      .xxx.
      .xxx.
      .....
    duration: 0.3
```

### Spots

Spot modules have no pixels. With no `spots` key they take the average colour of the frame. To set them, give one colour per spot module, in chain order:

```yaml
spots: ['#ff0000', '#0000ff']
```

### Fit

When an art is smaller than the cube canvas, the `show` action's `fit` option decides what happens:

| `fit` | Result |
| --- | --- |
| `error` | The action fails. This is the default. |
| `center` | The art sits in the middle, the rest is black. |
| `tile` | The art repeats to fill the canvas. |

An art bigger than the canvas always fails. The background always uses `center`.

## Presets

A preset is a named art. Names are found in three places. If the same name is in more than one place, the first one wins:

1. **Saved presets**: added in the integration options (stored by Home Assistant).
2. **Preset files**: `*.yaml` files in the `cubematrix` folder of your Home Assistant config folder. A file holds one art or a list of arts. After a change, run the `cubematrix.reload_presets` action. A broken file is skipped and logged.
3. **Built-in**: shipped with the integration.

Built-in arts, all 5x5:

- `arrow_up`, `arrow_down`, `arrow_left`, `arrow_right`: a running arrow with one empty line between arrows
- `blinds_open`, `blinds_close`: slats in a light and a dark colour, one after the other
- `check` (green), `check_red`
- `cross_red`, `cross_green`

## Generated effects

These need no art, they are calculated for any canvas size. Use them as a preset name:

| Name | Look |
| --- | --- |
| `fire` | Flames. This is the default background. |
| `rainbow` | A moving band of colours. |
| `plasma` | Slow flowing waves. |

An effect plays for 5 seconds by default. Use `duration` to change that, or `repeat: forever` to let it run.

## Actions

All actions except `reload_presets` are used on cube lights (`target: entity_id: light.my_cube`).

### `cubematrix.show`

Plays an art or effect on top of the background. When it ends, the cube goes on as `then` says.

| Field | Meaning | Default |
| --- | --- | --- |
| `preset` | Name of a preset. Give `preset` or `definition`, not both. | |
| `definition` | One art in the pixel art format, as YAML text or a mapping. | |
| `repeat` | How many times an animation plays: `1`, `2`, `3`, any number, or `forever`. | `1` |
| `duration` | How long the show lasts, in seconds. Ends the show even if the animation is not finished. | still picture or effect: 5 s; animation: until `repeat` is done |
| `fps` | Speed of the animation, instead of the art's own `fps`. | the art's `fps` |
| `then` | What comes after: `restore` (the background), `hold` (keep the last frame), `off` (black), or a preset name (shown until something else is played). | `restore` |
| `fit` | `error`, `center` or `tile`. | `error` |
| `wait` | If `true`, the action finishes only when the show part ends (not the `then` part). | `false` |
| `wake` | If `true`, the show also plays when the cube light is off. The light stays off, and the lamp goes off again when the show ends (`then` is not used). Without it, a show on a cube that is off does nothing. | `false` |

With `repeat: forever` and no `duration`, the show never ends by itself. Use `stop` or another `show`. A new `show` replaces the current one.

### `cubematrix.set_background`

| Field | Meaning |
| --- | --- |
| `preset` | Name of a preset, or `off` for black. Needed. |

The same can be done with the Background select. A new cube starts with `fire`.

### `cubematrix.stop`

Ends the current show and goes back to the background.

### `cubematrix.reload_presets`

Reads the preset files again. It has no target.

## Automation examples

### Desk up / down arrow

```yaml
automation:
  - alias: Desk arrow
    triggers:
      - trigger: state
        entity_id: cover.desk
        attribute: current_position
    actions:
      - action: cubematrix.show
        target:
          entity_id: light.desk_cube
        data:
          preset: >-
            {{ 'arrow_up' if trigger.to_state.attributes.current_position
               > trigger.from_state.attributes.current_position else 'arrow_down' }}
          repeat: 3
```

### An arrow running over two cubes

`wait: true` on the first `show` makes the action wait until the arrow has left the first cube. Then the second cube starts.

```yaml
actions:
  - action: cubematrix.show
    target:
      entity_id: light.cube_left
    data:
      preset: arrow_right
      wait: true
  - action: cubematrix.show
    target:
      entity_id: light.cube_right
    data:
      preset: arrow_right
```

### Fire by day, off at night

Use fixed times. The `fire` effect is the background in the day and the cube is black at night.

```yaml
automation:
  - alias: Cube fire in the morning
    triggers:
      - trigger: time
        at: "07:00:00"
    actions:
      - action: cubematrix.set_background
        target:
          entity_id: light.desk_cube
        data:
          preset: fire

  - alias: Cube off at night
    triggers:
      - trigger: time
        at: "23:00:00"
    actions:
      - action: cubematrix.set_background
        target:
          entity_id: light.desk_cube
        data:
          preset: "off"
```

### Play your own art once

```yaml
actions:
  - action: cubematrix.show
    target:
      entity_id: light.desk_cube
    data:
      duration: 10
      then: hold
      definition:
        name: heart
        size: 5x5
        palette: {r: '#ff0030'}
        frames:
          - |
            .r.r.
            rrrrr
            rrrrr
            .rrr.
            ..r..
```

## Troubleshooting

**The colour changes by itself, or the picture disappears.**
Matter, the Yeelight app or another integration changed the lamp's colour. The lamp then leaves direct mode. The integration notices this and recovers, but there can be a short flash. A still picture (for example the `off` background) is checked and sent again every 30 seconds, so it can take up to 30 seconds to come back. The same check finds a lamp that lost power: the light becomes unavailable and comes back by itself. Do not control the same lamp from other apps or integrations while the cube is in use.

**The lamp ignores frames, or the cube stays dark or shows wrong pixels.**
- Check that **LAN Control** is on in the Yeelight app.
- Check that the IP in the integration is the lamp's current IP (use the DHCP reservation).
- Check the module layout in the options: the order and the number of `matrix` and `spot` modules must match your chain.
- Make sure the light is on.

**Animations are slow (about 1 frame per second) and the log says the UDP session doesn't work.**
The lamp didn't answer on UDP port 55444, so the integration sends frames over LAN control. Check that nothing between Home Assistant and the lamp blocks UDP to port 55444 (for example a firewall that blocks outgoing traffic, or a different network without a route). After you fix it, reload the integration, or wait: while the background plays, UDP is tried again every 5 minutes. A layout of more than about 6 matrices is always slow, see [Connection](#connection).

**The cube blinks for a moment now and then.**
The lamp left direct mode by itself and the integration put it back. See [Connection](#connection).

## Development

The tests need Python 3.14 (Home Assistant 2026.10). Run them from the integration folder:

```sh
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt && .venv/bin/pytest
```

The core modules (`artformat.py`, `layout.py`, `effects.py`, `presets.py`, `protocol.py`, `connection.py`, `player.py`) do not import Home Assistant, so they can be tested alone.

## License

MIT, see `LICENSE`.
