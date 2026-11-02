"""Snake -- the improved rewrite, still meant to be read as much as played.

Run it with:
    python snake.py               (needs Python 3.9+ and tkinter)

Keys:
    Arrow keys or W A S D   steer
    SPACE                   start / pause / resume
    R                       restart with the same settings
    M                       back to the menu
    ESC                     quit
  In the menu only:
    1 / 2 / 3               EASY / NORMAL / HARD
    T                       toggle wrap-around (teleport through walls)
    O                       toggle obstacles (a few new walls every level)

How this file is organised:

    1. TUNABLES     every magic number, once
    2. PURE RULES   advance() and friends. State in, state out. No turtle,
                    no clock, and randomness only through an `rng` argument.
                    This section is what a test file would exercise.
    3. THE GAME     SnakeGame: turns state into pixels, runs the loop,
                    turns key presses into queued intentions.
    4. ENTRY POINT

The one idea: state -> rules -> new state, then state -> pixels.
State is now a frozen dataclass, so a bug can never come from a rule
quietly mutating something it was handed.
"""

from __future__ import annotations

import json
import random
import threading
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from pathlib import Path
from typing import Optional

# turtle needs tkinter, which some machines (and test runners) don't have.
# The rules below don't need either, so importing this module never fails.
try:
    import tkinter
    import turtle
except ImportError:                       # pragma: no cover
    tkinter = None
    turtle = None

try:
    import winsound                       # Windows only
except ImportError:
    winsound = None


# ---------------------------------------------------------------------------
# 1. TUNABLES
# ---------------------------------------------------------------------------

CELL = 20                      # pixels per grid square
COLS, ROWS = 29, 25            # board size in cells -> 580 x 500 px
FRAME = 12                     # thickness of the black border, in pixels

START_LEN = 3
POINTS_PER_LEVEL = 5
SPEED_DECAY = 0.93             # each cell of growth multiplies the slack by this:
                               # fast early ramp-up, then a plateau at the minimum

WRAP_AROUND = False            # default for the menu toggle

BONUS_CHANCE = 0.25            # chance a bonus item appears after normal food
BONUS_LIFETIME = 40            # ticks before an uneaten bonus vanishes
BONUS_POINTS = 3

OBSTACLES_PER_LEVEL = 3        # new wall cells added at each level-up
MAX_OBSTACLES = 30
OBSTACLE_MIN_DISTANCE = 6      # never drop a wall right in front of the head
OBSTACLE_TRIES = 60            # candidate cells tried per level-up

DEATH_FLASHES = 6              # head/body blink this many frames on death
IDLE_DELAY_MS = 60             # polling rate while paused / in menu / dead
SOUND = True                   # beeps on Windows; silently ignored elsewhere

BG_COLOR = "#1f7a2a"
TILE_A = "#2cdb35"
TILE_B = "#26c42e"
HEAD_COLOR = "#0b0b0b"
BODY_COLOR = "#f7f7f7"         # body fades from this ...
BODY_TAIL = "#98a89a"          # ... to this at the tail
FOOD_COLOR = "#e01b1b"
BONUS_COLOR = "#f5d90a"
OBSTACLE_COLOR = "#4a4a4a"
DEAD_COLOR = "#e01b1b"
TEXT_COLOR = "#0b0b0b"
ACCENT = "#f5d90a"
PANEL_BG = "#0f3d14"           # the dark box behind menu / pause / game-over text
PANEL_TEXT = "#f7f7f7"
PANEL_WIDTH = 440


@dataclass(frozen=True)
class Difficulty:
    name: str
    start_delay_ms: int        # milliseconds per tick at the start (lower = faster)
    min_delay_ms: int          # fastest the game ever gets


DIFFICULTIES = {
    "1": Difficulty("EASY", 110, 60),
    "2": Difficulty("NORMAL", 85, 42),
    "3": Difficulty("HARD", 65, 32),
}

SCORE_FILE = Path(__file__).with_name("snake_highscore.json")

LEFT = -COLS * CELL / 2
TOP = ROWS * CELL / 2

# Row 0 is the TOP of the board and rows grow downward, while turtle's y grows
# upward. So "up" on screen is row - 1.
KEY_DIRECTIONS = {
    "Up":    (0, -1), "w": (0, -1),
    "Down":  (0, 1),  "s": (0, 1),
    "Left":  (-1, 0), "a": (-1, 0),
    "Right": (1, 0),  "d": (1, 0),
}
DIRECTIONS = ((0, -1), (0, 1), (-1, 0), (1, 0))

Cell = tuple[int, int]


# ---------------------------------------------------------------------------
# 2. PURE RULES
# ---------------------------------------------------------------------------

class Phase(Enum):
    MENU = auto()
    PLAYING = auto()
    PAUSED = auto()
    DEAD = auto()
    WON = auto()


class Event(Enum):
    MOVED = auto()
    ATE = auto()               # normal food
    ATE_BONUS = auto()
    DIED = auto()


@dataclass(frozen=True)
class GameState:
    """Everything the rules need. Frozen: rules return a new one."""
    body: tuple[Cell, ...]                     # body[0] is the head
    direction: Cell
    food: Optional[Cell]
    bonus: Optional[Cell] = None
    bonus_ttl: int = 0
    obstacles: frozenset = field(default_factory=frozenset)
    score: int = 0
    phase: Phase = Phase.PLAYING

    @property
    def level(self) -> int:
        return 1 + self.score // POINTS_PER_LEVEL


def add(a: Cell, b: Cell) -> Cell:
    return (a[0] + b[0], a[1] + b[1])


def in_bounds(cell: Cell) -> bool:
    return 0 <= cell[0] < COLS and 0 <= cell[1] < ROWS


def wrap_cell(cell: Cell) -> Cell:
    return (cell[0] % COLS, cell[1] % ROWS)


def is_opposite(a: Cell, b: Cell) -> bool:
    return a[0] == -b[0] and a[1] == -b[1]


def cell_xy(cell: Cell) -> tuple[float, float]:
    """Grid cell -> screen pixels. The ONLY place this conversion happens."""
    col, row = cell
    return (LEFT + CELL / 2 + col * CELL, TOP - CELL / 2 - row * CELL)


def blend(c1: str, c2: str, t: float) -> str:
    """Linear mix of two '#rrggbb' colours; t=0 gives c1, t=1 gives c2."""
    t = max(0.0, min(1.0, t))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#{:02x}{:02x}{:02x}".format(
        *(round(x + (y - x) * t) for x, y in zip(a, b)))


def delay_ms(length: int, difficulty: Difficulty) -> int:
    """Smooth speed curve: gentle at first, flattening at the minimum."""
    slack = difficulty.start_delay_ms - difficulty.min_delay_ms
    eaten = max(0, length - START_LEN)
    return round(difficulty.min_delay_ms + slack * SPEED_DECAY ** eaten)


def queue_turn(current: Cell, pending: tuple[Cell, ...], new: Cell,
               limit: int = 2) -> tuple[Cell, ...]:
    """Add a turn to the input queue, or return it unchanged if it's useless.

    Compare against the LAST QUEUED direction, not the last applied one: that
    stops "Up then Left inside one tick" from folding the snake into itself.
    """
    reference = pending[-1] if pending else current
    if new == reference or is_opposite(new, reference) or len(pending) >= limit:
        return pending
    return pending + (new,)


def free_cells(state: GameState) -> list[Cell]:
    taken = set(state.body) | state.obstacles
    taken.update(c for c in (state.food, state.bonus) if c is not None)
    return [(c, r) for c in range(COLS) for r in range(ROWS) if (c, r) not in taken]


def advance(state: GameState, direction: Cell,
            wrap: bool = WRAP_AROUND) -> tuple[GameState, Event]:
    """Run one tick of the rules.

    Returns (new_state, event). On death the body is left untouched so the
    final frame can still be drawn. After eating normal food, `food` is None:
    the caller places the next one with place_food(), which needs an rng.
    """
    head = add(state.body[0], direction)
    if wrap:
        head = wrap_cell(head)

    dead = replace(state, direction=direction, phase=Phase.DEAD)
    if not in_bounds(head):
        return dead, Event.DIED

    ate_food = head == state.food
    ate_bonus = state.bonus is not None and head == state.bonus
    growing = ate_food or ate_bonus

    # When NOT growing, the tail vacates its cell this tick, so stepping into
    # the tail's old cell is legal.
    blocked = set(state.body) | state.obstacles
    if not growing:
        blocked.discard(state.body[-1])
    if head in blocked:
        return dead, Event.DIED

    body = (head,) + state.body
    if not growing:
        body = body[:-1]

    bonus, ttl = state.bonus, state.bonus_ttl
    if ate_bonus:
        bonus, ttl = None, 0
    elif bonus is not None:
        ttl -= 1
        if ttl <= 0:
            bonus, ttl = None, 0

    score = state.score + (1 if ate_food else 0) + (BONUS_POINTS if ate_bonus else 0)
    event = (Event.ATE_BONUS if ate_bonus else Event.ATE if ate_food else Event.MOVED)

    return replace(state, body=body, direction=direction,
                   food=None if ate_food else state.food,
                   bonus=bonus, bonus_ttl=ttl, score=score), event


def place_food(state: GameState, rng: random.Random) -> GameState:
    """Pick uniformly from free cells. A full board means the game is won.

    Building the free list can't loop forever, unlike "pick random cells
    until one is empty" on a nearly full board.
    """
    free = free_cells(state)
    if not free:
        return replace(state, food=None, phase=Phase.WON)
    return replace(state, food=rng.choice(free))


def maybe_spawn_bonus(state: GameState, rng: random.Random,
                      chance: float = BONUS_CHANCE) -> GameState:
    if state.bonus is not None or rng.random() >= chance:
        return state
    free = free_cells(state)
    if not free:
        return state
    return replace(state, bonus=rng.choice(free), bonus_ttl=BONUS_LIFETIME)


def reachable_cells(body: tuple[Cell, ...], obstacles: frozenset,
                    wrap: bool = False) -> set[Cell]:
    """Flood-fill from the head over cells that are neither snake nor wall."""
    blocked = set(body) | obstacles
    start = body[0]
    seen = {start}
    stack = [start]
    while stack:
        cell = stack.pop()
        for d in DIRECTIONS:
            nxt = add(cell, d)
            if wrap:
                nxt = wrap_cell(nxt)
            elif not in_bounds(nxt):
                continue
            if nxt in blocked or nxt in seen:
                continue
            seen.add(nxt)
            stack.append(nxt)
    seen.discard(start)
    return seen


def add_obstacles(state: GameState, rng: random.Random,
                  count: int = OBSTACLES_PER_LEVEL,
                  min_distance: int = OBSTACLE_MIN_DISTANCE,
                  wrap: bool = False) -> GameState:
    """Drop up to `count` wall cells, never in a way that makes the level unfair.

    A wall is rejected if it is too close to the head, or if it would cut off
    any part of the free board (checked with a flood fill).
    """
    want = min(count, MAX_OBSTACLES - len(state.obstacles))
    if want <= 0:
        return state

    hx, hy = state.body[0]
    candidates = [c for c in free_cells(state)
                  if abs(c[0] - hx) + abs(c[1] - hy) >= min_distance]
    rng.shuffle(candidates)

    obstacles = state.obstacles
    reach = reachable_cells(state.body, obstacles, wrap)
    added = 0
    for cell in candidates[:OBSTACLE_TRIES]:
        if added >= want:
            break
        trial = obstacles | {cell}
        after = reachable_cells(state.body, trial, wrap)
        if len(after) == len(reach) - 1:      # only the wall itself disappeared
            obstacles, reach = trial, after
            added += 1
    return replace(state, obstacles=obstacles)


def new_game(rng: random.Random) -> GameState:
    row, col = ROWS // 2, COLS // 3
    body = tuple((col - i, row) for i in range(START_LEN))
    return place_food(GameState(body=body, direction=(1, 0), food=None), rng)


def score_key(difficulty: Difficulty, wrap: bool, obstacles: bool) -> str:
    """High scores are kept per setting combination, so they're comparable."""
    return difficulty.name + ("-WRAP" if wrap else "") + ("-OBS" if obstacles else "")


def load_highscores(path: Path = SCORE_FILE) -> dict[str, int]:
    """Any problem at all means 'no scores yet'. Old files held one bare int."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, int) and not isinstance(data, bool):
        return {"NORMAL": data}
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items()
                if isinstance(v, int) and not isinstance(v, bool)}
    return {}


def save_highscores(scores: dict[str, int], path: Path = SCORE_FILE) -> None:
    """Temp file then rename, so a crash can't leave half a file."""
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(scores), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass                       # a missing high score must never kill the game


def beep(freq: int, ms: int) -> None:
    """Fire-and-forget tone. Beep() blocks, so it gets its own thread."""
    if not SOUND or winsound is None:
        return

    def _play():
        try:
            winsound.Beep(freq, ms)
        except (RuntimeError, OSError):
            pass
    threading.Thread(target=_play, daemon=True).start()


# ---------------------------------------------------------------------------
# 3. THE GAME OBJECT: settings + rendering + the loop + input
# ---------------------------------------------------------------------------

class SnakeGame:
    def __init__(self):
        if turtle is None:
            raise SystemExit("This game needs tkinter (it ships with most "
                             "Python installs; on Linux try: sudo apt install python3-tk)")
        self.screen = turtle.Screen()
        self.screen.title("Snake  |  arrows/WASD  |  SPACE pause  |  R restart  |  M menu  |  ESC quit")
        self.screen.setup(760, 740)
        self.screen.bgcolor(BG_COLOR)
        self.screen.tracer(0)      # we repaint ourselves, once per changed frame

        self.rng = random.Random()
        self.running = True        # the window still exists
        self.dirty = True          # something changed, so render() is due
        self.flash = 0             # death-blink frames left
        self.run_active = False    # a scored run is in progress (see end_run)

        self.difficulty = DIFFICULTIES["2"]
        self.wrap = WRAP_AROUND
        self.obstacles_on = False
        self.highscores = load_highscores()

        self.pending: tuple[Cell, ...] = ()
        self.state = replace(new_game(self.rng), phase=Phase.MENU)

        self._make_turtles()
        self._draw_board()
        self._bind_keys()
        self.tick()

    # -- rendering objects ---------------------------------------------------

    def _new_pen(self, shape=None, color=TEXT_COLOR, size=None):
        pen = turtle.Turtle(visible=False)
        pen.speed(0)
        pen.penup()
        pen.color(color)
        if shape:
            pen.shape(shape)
        if size:
            pen.shapesize(size)
        return pen

    def _make_turtles(self):
        # Stamping pens: one turtle draws the whole body / all walls.
        self.body_pen = self._new_pen("circle", BODY_COLOR, 0.95)
        self.obstacle_pen = self._new_pen("square", OBSTACLE_COLOR, 0.95)
        self.head_pen = self._new_pen("circle", HEAD_COLOR, 1.05)
        self.eye_pen = self._new_pen()
        self.food_pen = self._new_pen("circle", FOOD_COLOR, 0.8)
        self.bonus_pen = self._new_pen("turtle", BONUS_COLOR, 0.9)
        self.score_pen = self._new_pen()
        self.info_pen = self._new_pen()
        self.panel_pen = self._new_pen()        # created before message_pen so text sits on top
        self.message_pen = self._new_pen()

    @staticmethod
    def _filled_rect(pen, x, y, width, height, color):
        """Filled rectangle from its top-left corner (y grows downward)."""
        pen.goto(x, y)
        pen.fillcolor(color)
        pen.pendown()
        pen.begin_fill()
        for _ in range(2):
            pen.forward(width)
            pen.right(90)
            pen.forward(height)
            pen.right(90)
        pen.end_fill()
        pen.penup()

    def _draw_board(self):
        """Static scenery: drawn once, never redrawn."""
        pen = self._new_pen()
        self._filled_rect(pen, LEFT - FRAME, TOP + FRAME,
                          COLS * CELL + 2 * FRAME, ROWS * CELL + 2 * FRAME, "black")
        self._filled_rect(pen, LEFT - 5, TOP + 5,
                          COLS * CELL + 10, ROWS * CELL + 10, ACCENT)

        # Checkerboard: two stamping pens, one per colour, stamped once.
        tiles = {0: self._new_pen("square", TILE_A, CELL / 20),
                 1: self._new_pen("square", TILE_B, CELL / 20)}
        for col in range(COLS):
            for row in range(ROWS):
                pen_ = tiles[(col + row) % 2]
                pen_.goto(*cell_xy((col, row)))
                pen_.stamp()

        self.score_pen.goto(0, TOP + FRAME + 36)
        self.info_pen.goto(0, -(TOP + FRAME + 44))
        self.message_pen.goto(0, -20)

    def _draw_eyes(self, flashing):
        """Two eyes on the head, offset along the direction of travel."""
        self.eye_pen.clear()
        x, y = cell_xy(self.state.body[0])
        dc, dr = self.state.direction
        fx, fy = dc, -dr                    # forward, in turtle (y-up) space
        px, py = -fy, fx                    # perpendicular
        for side in (-1, 1):
            ex = x + fx * 4 + px * side * 4.5
            ey = y + fy * 4 + py * side * 4.5
            self.eye_pen.goto(ex, ey)
            self.eye_pen.dot(7, DEAD_COLOR if flashing else "white")
            self.eye_pen.goto(ex + fx * 1.5, ey + fy * 1.5)
            self.eye_pen.dot(3, "black")

    def _overlay_lines(self):
        """What to show in the centre panel: a list of (text, font size, colour)."""
        s = self.state
        on = lambda flag: "ON" if flag else "OFF"
        if s.phase is Phase.MENU:
            return [
                ("SNAKE", 34, ACCENT),
                ("1 EASY   2 NORMAL   3 HARD", 14, PANEL_TEXT),
                ("difficulty:  " + self.difficulty.name, 14, ACCENT),
                ("T   wrap-around:  " + on(self.wrap), 14, PANEL_TEXT),
                ("O   obstacles:  " + on(self.obstacles_on), 14, PANEL_TEXT),
                ("SPACE  to start", 18, ACCENT),
            ]
        if s.phase is Phase.PAUSED:
            return [("PAUSED", 30, ACCENT),
                    ("SPACE to resume", 14, PANEL_TEXT)]
        if s.phase is Phase.DEAD:
            return [("GAME OVER", 30, "#ff6a6a"),
                    ("score  {}".format(s.score), 18, PANEL_TEXT),
                    ("R  restart      M  menu", 14, PANEL_TEXT)]
        if s.phase is Phase.WON:
            return [("YOU FILLED THE BOARD!", 20, ACCENT),
                    ("R  restart      M  menu", 14, PANEL_TEXT)]
        return []

    def _show_overlay(self, lines):
        """Draw a dark panel in the middle of the board with each line centred.

        Every line is written on its own, because turtle centres a multi-line
        string as one block and left-aligns the lines inside it.
        """
        self.panel_pen.clear()
        self.message_pen.clear()
        if not lines:
            return
        slots = [round(size * 2.1) for _, size, _ in lines]    # px per line
        height = sum(slots) + 36
        top = height / 2
        self.panel_pen.pencolor(ACCENT)
        self.panel_pen.pensize(3)
        self._filled_rect(self.panel_pen, -PANEL_WIDTH / 2, top,
                          PANEL_WIDTH, height, PANEL_BG)
        y = top - 18                                           # inside the padding
        for (text, size, color), slot in zip(lines, slots):
            y -= slot
            self.message_pen.color(color)
            self.message_pen.goto(0, y + size * 0.45)          # text sits on its baseline
            self.message_pen.write(text, align="center",
                                   font=("Consolas", size, "bold"))

    def render(self):
        """State -> pixels. Reads everything, writes nothing to the state."""
        s = self.state
        flashing = self.flash % 2 == 1

        if s.phase is Phase.MENU:
            self._hide_world()             # menu on a clean board, no snake behind it
        else:
            self._draw_world(s, flashing)

        best = max(self.highscores.get(self._key(), 0), s.score)
        self.score_pen.clear()
        self.score_pen.write("SCORE {}      LEVEL {}      BEST {}".format(
            s.score, s.level, best), align="center", font=("Consolas", 20, "bold"))

        self.info_pen.clear()
        self.info_pen.write("{}   wrap {}   obstacles {}".format(
            self.difficulty.name, "on" if self.wrap else "off",
            "on" if self.obstacles_on else "off").lower(),
            align="center", font=("Consolas", 12, "normal"))

        # Drawn last, so the panel is the newest thing on the canvas (= on top).
        self._show_overlay(self._overlay_lines())
        self.screen.update()      # tracer(0): nothing shows until this

    def _hide_world(self):
        self.body_pen.clearstamps()
        self.obstacle_pen.clearstamps()
        self.eye_pen.clear()
        for pen in (self.head_pen, self.food_pen, self.bonus_pen):
            pen.hideturtle()

    def _draw_world(self, s, flashing):
        """Snake, eyes, walls, food and bonus."""
        self.body_pen.clearstamps()
        n = len(s.body)
        for i, cell in enumerate(s.body[1:], start=1):
            self.body_pen.color(DEAD_COLOR if flashing
                                else blend(BODY_COLOR, BODY_TAIL, i / max(1, n - 1)))
            self.body_pen.goto(*cell_xy(cell))
            self.body_pen.stamp()

        self.head_pen.color(DEAD_COLOR if flashing else HEAD_COLOR)
        self.head_pen.goto(*cell_xy(s.body[0]))
        self.head_pen.showturtle()
        self._draw_eyes(flashing)

        self.obstacle_pen.clearstamps()
        for cell in s.obstacles:
            self.obstacle_pen.goto(*cell_xy(cell))
            self.obstacle_pen.stamp()

        if s.food is not None:
            self.food_pen.goto(*cell_xy(s.food))
            self.food_pen.showturtle()
        else:
            self.food_pen.hideturtle()

        # Bonus blinks during its last few ticks to warn that it's leaving.
        if s.bonus is not None and not (s.bonus_ttl <= 10 and s.bonus_ttl % 2 == 0):
            self.bonus_pen.goto(*cell_xy(s.bonus))
            self.bonus_pen.showturtle()
        else:
            self.bonus_pen.hideturtle()

    # -- settings and run bookkeeping ---------------------------------------

    def _key(self):
        return score_key(self.difficulty, self.wrap, self.obstacles_on)

    def start_game(self):
        self.end_run()
        self.state = new_game(self.rng)
        self.pending = ()
        self.flash = 0
        self.run_active = True
        self.dirty = True

    def end_run(self):
        """Bank the score. Safe to call many times: it only counts once."""
        if not self.run_active:
            return
        self.run_active = False
        key = self._key()
        if self.state.score > self.highscores.get(key, 0):
            self.highscores[key] = self.state.score
            save_highscores(self.highscores)

    # -- the loop ------------------------------------------------------------

    def next_delay(self):
        if self.state.phase is Phase.PLAYING:
            return delay_ms(len(self.state.body), self.difficulty)
        return IDLE_DELAY_MS

    def tick(self):
        """One frame. Reschedules itself; no `while True` anywhere."""
        if not self.running:
            return
        try:
            if self.state.phase is Phase.PLAYING:
                self.step()
                self.dirty = True
            elif self.state.phase is Phase.DEAD and self.flash > 0:
                self.flash -= 1
                self.dirty = True
            if self.dirty:                     # skip repainting identical frames
                self.render()
                self.dirty = False
            self.screen.ontimer(self.tick, self.next_delay())
        except (turtle.Terminator, tkinter.TclError):
            self.running = False               # window closed under a pending timer

    def step(self):
        """Apply one tick of the rules, then the things that need randomness."""
        direction = self.pending[0] if self.pending else self.state.direction
        self.pending = self.pending[1:]

        old_level = self.state.level
        new, event = advance(self.state, direction, self.wrap)

        if event is Event.DIED:
            self.state = new
            self.flash = DEATH_FLASHES
            self.end_run()
            beep(180, 300)
            return

        if event is Event.ATE:
            new = place_food(new, self.rng)
            beep(880, 40)
        elif event is Event.ATE_BONUS:
            beep(1320, 90)

        if new.phase is Phase.PLAYING:
            if self.obstacles_on and new.level > old_level:
                new = add_obstacles(new, self.rng, wrap=self.wrap)
            if event is Event.ATE:
                new = maybe_spawn_bonus(new, self.rng)

        self.state = new
        if new.phase is Phase.WON:
            self.end_run()

    # -- input ---------------------------------------------------------------

    def queue_direction(self, new):
        if self.state.phase is Phase.PLAYING:
            self.pending = queue_turn(self.state.direction, self.pending, new)

    def space(self):
        phase = self.state.phase
        if phase is Phase.MENU:
            self.start_game()
        elif phase in (Phase.PLAYING, Phase.PAUSED):
            self.state = replace(self.state, phase=(
                Phase.PAUSED if phase is Phase.PLAYING else Phase.PLAYING))
            self.dirty = True

    def restart(self):
        if self.state.phase is not Phase.MENU:
            self.start_game()

    def to_menu(self):
        self.end_run()
        self.state = replace(new_game(self.rng), phase=Phase.MENU)
        self.pending = ()
        self.flash = 0
        self.dirty = True

    def menu_choice(self, key):
        """Settings can only change in the menu, so a run's score key is stable."""
        if self.state.phase is not Phase.MENU:
            return
        if key in DIFFICULTIES:
            self.difficulty = DIFFICULTIES[key]
        elif key == "t":
            self.wrap = not self.wrap
        elif key == "o":
            self.obstacles_on = not self.obstacles_on
        self.dirty = True

    def quit_game(self):
        self.end_run()             # a new best must survive pressing ESC
        self.running = False
        self.screen.bye()

    def _bind_keys(self):
        screen = self.screen
        for key, direction in KEY_DIRECTIONS.items():
            # d=direction captures the CURRENT value (late-binding closure bug).
            screen.onkey(lambda d=direction: self.queue_direction(d), key)
        for key in list(DIFFICULTIES) + ["t", "o"]:
            screen.onkey(lambda k=key: self.menu_choice(k), key)
        screen.onkey(self.space, "space")
        screen.onkey(self.space, "Return")
        screen.onkey(self.restart, "r")
        screen.onkey(self.to_menu, "m")
        screen.onkey(self.quit_game, "Escape")
        screen.listen()


# ---------------------------------------------------------------------------
# 4. ENTRY POINT
# ---------------------------------------------------------------------------

def main():
    game = SnakeGame()
    try:
        game.screen.mainloop()
    except (turtle.Terminator, tkinter.TclError):
        pass                       # ESC closed the window out from under us


if __name__ == "__main__":
    main()