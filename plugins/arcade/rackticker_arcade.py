"""Ambient arcade: real games, played live by simple AIs at native resolution.

Nothing is a canned animation. Each game is a fixed-timestep simulation seeded
at random, so every run plays out differently: the Tetris bot stacks and
eventually tops out, the snake grows until it traps itself, Pong rallies speed
up until someone misses. All drawing lands on whole 1×1 or 2×2 pixel cells.
"""
from __future__ import annotations

from collections import deque
from functools import lru_cache
import math
import random

from PIL import Image, ImageDraw

from rackticker import Plugin, Module, new_frame
from app.core.fonts import draw_text, draw_tiny, text_width, tiny_width
from app.core.fx import Lettering, Particles, dim, ease_in_out, hsv, mix, sprite, stamp

STEP = 1 / 60
WHITE = (236, 240, 240)


class Game:
    title = "GAME"
    gravity = 40.0
    best = 0

    def __init__(self, rng):
        self.rng = rng
        self.particles = Particles()
        self.clock = 0.0
        self.score = 0
        # Completed rounds (game over, board cleared): the arcade and playlist
        # change scenes only at these natural breaks, never mid-play.
        self.rounds = 0
        self.reset()

    def reset(self):
        pass

    def restart(self):
        self.rounds += 1
        self.reset()

    def update(self, dt):
        self.clock += dt
        self.tick(dt)
        type(self).best = max(type(self).best, self.score)
        self.particles.step(dt, self.gravity, drag=.8)

    def burst(self, x, y, colors, count=14, speed=38):
        self.particles.burst(self.rng, x, y, count, speed, colors, life=(.3, .9))


# --- Tetris -------------------------------------------------------------------

ROWS, COLS, LEFT = 16, 10, 54
FULL = (1 << COLS) - 1
PIECES = {"I": ((0, 1), (1, 1), (2, 1), (3, 1)), "O": ((1, 0), (2, 0), (1, 1), (2, 1)),
          "T": ((1, 0), (0, 1), (1, 1), (2, 1)), "S": ((1, 0), (2, 0), (0, 1), (1, 1)),
          "Z": ((0, 0), (1, 0), (1, 1), (2, 1)), "J": ((0, 0), (0, 1), (1, 1), (2, 1)),
          "L": ((2, 0), (0, 1), (1, 1), (2, 1))}
PIECE_COLORS = {"I": (0, 220, 255), "O": (255, 214, 0), "T": (190, 80, 255), "S": (60, 230, 90),
                "Z": (255, 60, 60), "J": (60, 110, 255), "L": (255, 150, 30)}


def _rotations(cells):
    found, current = [], list(cells)
    for _ in range(4):
        low_x, low_y = min(x for x, _ in current), min(y for _, y in current)
        normal = tuple(sorted((x - low_x, y - low_y) for x, y in current))
        if normal not in found:
            found.append(normal)
        current = [(-y, x) for x, y in current]
    return found


ROTATIONS = {kind: _rotations(cells) for kind, cells in PIECES.items()}


def fits(rows, cells, col, row):
    for x, y in cells:
        cx, cy = col + x, row + y
        if cx < 0 or cx >= COLS or cy >= ROWS or (cy >= 0 and rows[cy] >> cx & 1):
            return False
    return True


def placement_score(rows, cells, col, row):
    """El-Tetris evaluation (Pierre Dellacherie's features with published
    weights): it keeps the stack flat and almost never buries an open column."""
    board = list(rows)
    for x, y in cells:
        board[row + y] |= 1 << (col + x)
    full = [index for index, value in enumerate(board) if value == FULL]
    eroded = len(full) * sum(1 for _, y in cells if row + y in full)
    board = [0] * len(full) + [value for value in board if value != FULL]
    landing = ROWS - row - max(y for _, y in cells) / 2
    row_transitions = column_transitions = holes = wells = 0
    for value in board:
        previous = 1
        for x in range(COLS):
            bit = value >> x & 1
            row_transitions += bit != previous
            previous = bit
        row_transitions += previous != 1
    for x in range(COLS):
        previous, covered, depth = 0, False, 0
        for y in range(ROWS):
            bit = board[y] >> x & 1
            column_transitions += bit != previous
            previous = bit
            if bit:
                covered, depth = True, 0
                continue
            if covered:
                holes += 1
            left = x == 0 or board[y] >> (x - 1) & 1
            right = x == COLS - 1 or board[y] >> (x + 1) & 1
            if left and right:
                depth += 1
                wells += depth
            else:
                depth = 0
        column_transitions += previous == 0
    return (-4.500158825082766 * landing + 3.4181268101392694 * eroded
            - 3.2178882868487753 * row_transitions - 9.348695305445199 * column_transitions
            - 7.899265427351652 * holes - 3.3855972247263626 * wells)


class Tetris(Game):
    title = "TETRIS"

    def reset(self):
        self.rows = [0] * ROWS
        self.colors = [[None] * COLS for _ in range(ROWS)]
        self.bag, self.lines, self.score = [], 0, 0
        self.flash, self.flash_time, self.over = [], 0.0, 0.0
        self.callout, self.callout_time = "", 0.0
        self.next = self._take()
        self.spawn()

    @property
    def level(self):
        return 1 + self.lines // 4

    def _take(self):
        if not self.bag:
            self.bag = list(PIECES)
            self.rng.shuffle(self.bag)
        return self.bag.pop()

    def spawn(self):
        self.kind, self.next = self.next, self._take()
        self.rot, self.col, self.row, self.wait = 0, 3, 0, .15
        self.fall = max(.012, .5 - self.level * .06)
        options = []
        for rot, cells in enumerate(ROTATIONS[self.kind]):
            width = max(x for x, _ in cells) + 1
            for col in range(COLS - width + 1):
                if not fits(self.rows, cells, col, 0):
                    continue
                row = 0
                while fits(self.rows, cells, col, row + 1):
                    row += 1
                options.append((placement_score(self.rows, cells, col, row), rot, col))
        if not options or not fits(self.rows, ROTATIONS[self.kind][0], self.col, 0):
            self.over = 1e-6
            return
        options.sort(reverse=True)
        # Plays the best placement; gravity speeding up by level is what
        # eventually tops it out, like a human running out of reaction time.
        _, self.target_rot, self.target_col = options[0]

    def tick(self, dt):
        self.callout_time = max(0.0, self.callout_time - dt)
        if self.over:
            self.over += dt
            if self.over > 2.2:
                self.restart()
            return
        if self.flash:
            self.flash_time -= dt
            if self.flash_time <= 0:
                keep = [i for i in range(ROWS) if i not in self.flash]
                count = len(self.flash)
                self.rows = [0] * count + [self.rows[i] for i in keep]
                self.colors = [[None] * COLS for _ in range(count)] + [self.colors[i] for i in keep]
                self.flash = []
                self.spawn()
            return
        shapes = ROTATIONS[self.kind]
        # Gravity runs on its own clock, even while the AI is still steering, and
        # outpaces its hand speed after a few levels: strong games still top out.
        aligned = self.col == self.target_col and self.rot == self.target_rot
        self.fall -= dt
        if self.fall <= 0:
            if not fits(self.rows, shapes[self.rot], self.col, self.row + 1):
                self.lock()
                return
            self.row += 1
            # Lined up: soft-drop like a confident player; otherwise fall at level speed.
            self.fall = .028 if aligned else max(.012, .5 - self.level * .06)
        self.wait -= dt
        if self.wait > 0 or aligned:
            return
        if self.rot != self.target_rot and fits(self.rows, shapes[self.target_rot], self.col, self.row):
            self.rot, self.wait = self.target_rot, .1
        elif self.col != self.target_col and fits(self.rows, shapes[self.rot],
                                                  self.col + (1 if self.target_col > self.col else -1), self.row):
            self.col += 1 if self.target_col > self.col else -1
            self.wait = .075
        else:
            self.wait = .075  # Blocked this instant; gravity keeps working meanwhile.

    def lock(self):
        color = PIECE_COLORS[self.kind]
        for x, y in ROTATIONS[self.kind][self.rot]:
            self.rows[self.row + y] |= 1 << (self.col + x)
            self.colors[self.row + y][self.col + x] = color
        full = [i for i, value in enumerate(self.rows) if value == FULL]
        if not full:
            self.score += 4
            self.spawn()
            return
        self.flash, self.flash_time = full, .45
        self.lines += len(full)
        self.score += (0, 100, 300, 500, 800)[len(full)] * self.level
        # Singles are routine for this AI; only multi-line clears earn a callout.
        if len(full) >= 2:
            self.callout = ("", "", "DOUBLE", "TRIPLE", "TETRIS!")[len(full)]
            self.callout_time = 1.6 if len(full) < 4 else 2.4
        for y in full:
            for x in range(0, COLS, 3 if len(full) < 4 else 1):
                self.burst(LEFT + x * 2 + 1, y * 2, (WHITE, PIECE_COLORS[self.rng.choice(list(PIECES))]),
                           2 if len(full) < 4 else 3, 30)

    @staticmethod
    def cell(draw, x, y, color):
        draw.point((x, y), fill=mix(color, (255, 255, 255), .45))
        draw.point((x + 1, y), fill=color)
        draw.point((x, y + 1), fill=color)
        draw.point((x + 1, y + 1), fill=dim(color, .55))

    def draw(self, frame):
        draw = ImageDraw.Draw(frame)
        wall = (48, 48, 90)
        draw.line((LEFT - 2, 0, LEFT - 2, 31), fill=wall)
        draw.line((LEFT + COLS * 2 + 1, 0, LEFT + COLS * 2 + 1, 31), fill=wall)
        topped = ROWS - int(self.over * 10)
        # Cleared rows wipe outward from the middle while flashing white.
        wiped = (1 - self.flash_time / .45) * (COLS / 2 + 1) if self.flash else 0
        for y in range(ROWS):
            flashing = y in self.flash
            for x in range(COLS):
                color = self.colors[y][x]
                if color:
                    if flashing:
                        if abs(x + .5 - COLS / 2) < wiped:
                            continue
                        color = WHITE
                    elif self.over and y >= topped:
                        color = (70, 70, 80)
                    self.cell(draw, LEFT + x * 2, y * 2, color)
        if not self.flash and not self.over:
            shape = ROTATIONS[self.kind][self.rot]
            ghost = self.row
            while fits(self.rows, shape, self.col, ghost + 1):
                ghost += 1
            if ghost > self.row + 1:
                for x, y in shape:
                    draw.point((LEFT + (self.col + x) * 2, (ghost + y) * 2), fill=dim(PIECE_COLORS[self.kind], .45))
                    draw.point((LEFT + (self.col + x) * 2 + 1, (ghost + y) * 2 + 1), fill=dim(PIECE_COLORS[self.kind], .45))
            for x, y in shape:
                self.cell(draw, LEFT + (self.col + x) * 2, (self.row + y) * 2, PIECE_COLORS[self.kind])
        muted = (130, 130, 190)
        draw_tiny(frame, "SCORE", 4, 2, muted)
        draw_tiny(frame, f"{self.score:06d}", 4, 9, WHITE)
        draw_tiny(frame, "LINES", 4, 18, muted)
        draw_tiny(frame, f"{self.lines:03d}", 4, 25, WHITE)
        draw_tiny(frame, f"LV{self.level}", 30, 25, (120, 220, 255))
        draw_tiny(frame, "NEXT", 82, 2, muted)
        for x, y in ROTATIONS[self.next][0]:
            self.cell(draw, 83 + x * 2, 10 + y * 2, PIECE_COLORS[self.next])
        if self.callout_time and (self.callout != "TETRIS!" or math.floor(self.clock * 8) % 2):
            color = (255, 214, 70) if self.callout == "TETRIS!" else (140, 230, 255)
            draw_tiny(frame, self.callout, 82, 22, color)
        else:
            draw_tiny(frame, "HI", 82, 19, muted)
            draw_tiny(frame, f"{Tetris.best:06d}", 82, 25, (255, 214, 70))
        if self.over and math.floor(self.clock * 4) % 2:
            draw.rectangle((LEFT - 1, 12, LEFT + COLS * 2, 18), fill=(0, 0, 0))
            draw_tiny(frame, "OVER", LEFT + 10 - tiny_width("OVER") // 2, 13, (255, 70, 70))


# --- Snake ----------------------------------------------------------------------

GW, GH = 63, 15
DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))


# A full course is this long; anything longer takes minutes to watch.
SNAKE_COURSE = 40


class Snake(Game):
    title = "SNAKE"
    gravity = 0.0

    def reset(self):
        start_y = self.rng.randrange(3, GH - 3)
        self.body = deque((8 - i, start_y) for i in range(5))
        self.food = self._food()
        self.timer, self.dead, self.score = 0.0, 0.0, 0

    def _food(self):
        occupied = set(self.body)
        free = [(x, y) for x in range(GW) for y in range(GH) if (x, y) not in occupied]
        return self.rng.choice(free) if free else None

    @staticmethod
    def _inside(point):
        return 0 <= point[0] < GW and 0 <= point[1] < GH

    def _path_step(self, start, goal, blocked):
        previous = {start: None}
        queue = deque([start])
        while queue:
            point = queue.popleft()
            if point == goal:
                while previous[point] != start:
                    point = previous[point]
                return point[0] - start[0], point[1] - start[1]
            for dx, dy in DIRECTIONS:
                nxt = (point[0] + dx, point[1] + dy)
                if self._inside(nxt) and nxt not in blocked and nxt not in previous:
                    previous[nxt] = point
                    queue.append(nxt)
        return None

    def _room(self, start, blocked, limit):
        seen, queue = {start}, deque([start])
        while queue and len(seen) < limit:
            point = queue.popleft()
            for dx, dy in DIRECTIONS:
                nxt = (point[0] + dx, point[1] + dy)
                if self._inside(nxt) and nxt not in blocked and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return len(seen)

    def _choose(self):
        head = self.body[0]
        blocked = set(list(self.body)[:-1])
        moves = [d for d in DIRECTIONS
                 if self._inside((head[0] + d[0], head[1] + d[1])) and (head[0] + d[0], head[1] + d[1]) not in blocked]
        if not moves:
            return None
        if self.rng.random() < .025:
            return self.rng.choice(moves)
        limit = len(self.body) + 24
        step = self.food and self._path_step(head, self.food, blocked)
        if step:
            nxt = (head[0] + step[0], head[1] + step[1])
            if self._room(nxt, blocked | {nxt}, limit) >= min(limit, len(self.body)):
                return step
        return max(moves, key=lambda d: self._room((head[0] + d[0], head[1] + d[1]),
                                                    blocked | {(head[0] + d[0], head[1] + d[1])}, limit))

    def tick(self, dt):
        if self.dead:
            self.dead += dt
            if self.dead > 2.0:
                self.restart()
            return
        self.timer -= dt
        if self.timer > 0:
            return
        self.timer = max(.045, .09 - len(self.body) * .0009)
        step = self._choose()
        head = self.body[0]
        nxt = step and (head[0] + step[0], head[1] + step[1])
        if not nxt or not self._inside(nxt) or nxt in set(list(self.body)[:-1]):
            self.dead = 1e-6
            for index, (x, y) in enumerate(self.body):
                if index % 3 == 0:
                    self.burst(2 + x * 2, 2 + y * 2, ((120, 255, 120), (40, 170, 70)), 2, 30)
            return
        self.body.appendleft(nxt)
        if nxt == self.food:
            self.score += 10
            self.burst(2 + nxt[0] * 2, 2 + nxt[1] * 2, ((255, 80, 60), (255, 220, 80)), 10)
            self.food = self._food()
            if self.food is None or len(self.body) >= SNAKE_COURSE:
                self.burst(2 + nxt[0] * 2, 2 + nxt[1] * 2, ((255, 214, 70), WHITE), 30, 50)
                self.restart()
        else:
            self.body.pop()

    def draw(self, frame):
        draw = ImageDraw.Draw(frame)
        draw.rectangle((0, 0, 127, 31), outline=(30, 40, 90))
        if not self.dead or (self.dead < 1 and math.floor(self.dead * 12) % 2):
            count = len(self.body)
            for index, (x, y) in enumerate(self.body):
                color = mix((190, 255, 120), (20, 110, 60), index / max(1, count - 1))
                draw.rectangle((1 + x * 2, 1 + y * 2, 2 + x * 2, 2 + y * 2), fill=color)
        if self.food:
            fx, fy = 1 + self.food[0] * 2, 1 + self.food[1] * 2
            draw.rectangle((fx, fy, fx + 1, fy + 1), fill=(255, 60, 50))
            if math.floor(self.clock * 4) % 2:
                draw.point((fx, fy), fill=(255, 220, 200))


# --- Breakout -------------------------------------------------------------------

BRICK_COLORS = ((255, 60, 60), (255, 150, 40), (255, 220, 50), (80, 220, 90), (60, 150, 255))


class Breakout(Game):
    title = "BREAKOUT"

    def reset(self):
        self.level, self.lives, self.score, self.over = 1, 3, 0, 0.0
        self.new_level()

    def new_level(self):
        self.bricks = {(c, r) for c in range(12) for r in range(5)}
        self.serve()

    def serve(self):
        self.ball = [62.0, 20.0]
        speed = 58 + self.level * 6
        angle = self.rng.uniform(.35, .75)
        self.vx = speed * math.sin(angle) * self.rng.choice((-1, 1))
        self.vy = -speed * math.cos(angle)
        self.paddle, self.wait = 56.0, 1.0
        self.error = self.rng.uniform(-5, 5)

    def _landing(self):
        x, y = self.ball
        if self.vy <= 0:
            return x
        px = x + self.vx * (27 - y) / self.vy
        span = 124
        mirrored = (px - 1) % (span * 2)
        return 1 + (mirrored if mirrored <= span else span * 2 - mirrored)

    def tick(self, dt):
        if self.over:
            self.over += dt
            if self.over > 2.2:
                self.restart()
            return
        if self.wait > 0:
            self.wait -= dt
            return
        for _ in range(2):
            if self._physics(dt / 2):
                return
        landing = self._landing()
        target = min(111.0, max(1.0, landing + 1 - 8 - self._aim(landing) * 9 + self.error))
        self.paddle += max(-110 * dt, min(110 * dt, target - self.paddle))

    def _aim(self, landing):
        """Paddle offset (-.85..85) that sends the ball toward the nearest brick.
        Without aiming, a centred return can bounce straight up forever beside
        the last few bricks."""
        if self.vy <= 0 or not self.bricks:
            return 0.0
        column, row = min(self.bricks, key=lambda brick: (abs(8 + brick[0] * 10 - landing), -brick[1]))
        dx = 8 + column * 10 - landing
        dy = max(4.0, 27 - (4 + row * 3))
        return max(-.85, min(.85, math.sin(math.atan2(dx, dy))))

    def _physics(self, dt):
        ball = self.ball
        ball[0] += self.vx * dt
        ball[1] += self.vy * dt
        if ball[0] < 1 or ball[0] > 125:
            ball[0] = min(125.0, max(1.0, ball[0]))
            self.vx = -self.vx
        if ball[1] < 1:
            ball[1] = 1.0
            self.vy = abs(self.vy)
        for column, row in self.bricks:
            x0, y0 = 4 + column * 10, 2 + row * 3
            if ball[0] + 2 > x0 and ball[0] < x0 + 9 and ball[1] + 2 > y0 and ball[1] < y0 + 2:
                self.bricks.discard((column, row))
                self.vy = -self.vy
                self.score += (5 - row) * 10
                self.burst(x0 + 4, y0 + 1, (BRICK_COLORS[row], WHITE), 8, 30)
                break
        if (self.vy > 0 and 27 <= ball[1] + 2 <= 30
                and ball[0] + 2 >= self.paddle and ball[0] <= self.paddle + 16):
            speed = min(135.0, math.hypot(self.vx, self.vy) * 1.02)
            offset = (ball[0] + 1 - (self.paddle + 8)) / 9
            self.vx = speed * max(-.85, min(.85, offset))
            self.vy = -math.sqrt(speed * speed - self.vx * self.vx)
            ball[1] = 27.0
            # Skilled but human: aims at a point on the paddle to angle shots, and
            # only occasionally mistimes one badly enough to lose the ball.
            self.error = self.rng.uniform(-2.5, 2.5) if self.rng.random() < .96 else self.rng.choice((-14, 14))
        if ball[1] > 33:
            self.lives -= 1
            if self.lives <= 0:
                self.over = 1e-6
            else:
                self.serve()
            return True
        if not self.bricks:
            self.level += 1
            for n in range(5):
                self.burst(self.rng.randrange(10, 118), self.rng.randrange(4, 20), (hsv(n / 5), WHITE), 16, 45)
            self.new_level()
            return True
        return False

    def draw(self, frame):
        draw = ImageDraw.Draw(frame)
        for column, row in self.bricks:
            x0, y0 = 4 + column * 10, 2 + row * 3
            draw.rectangle((x0, y0, x0 + 8, y0 + 1), fill=BRICK_COLORS[row])
            draw.line((x0, y0, x0 + 8, y0), fill=mix(BRICK_COLORS[row], (255, 255, 255), .35))
        paddle = round(self.paddle)
        draw.rectangle((paddle, 29, paddle + 15, 30), fill=(120, 190, 255))
        draw.line((paddle, 29, paddle + 15, 29), fill=(210, 235, 255))
        if not self.over:
            x, y = round(self.ball[0]), round(self.ball[1])
            draw.rectangle((x, y, x + 1, y + 1), fill=WHITE)
        for life in range(max(0, self.lives)):
            draw.point((126 - life * 3, 31), fill=(255, 90, 90))
        if self.wait > 0 or self.over:
            label = "GAME OVER" if self.over else f"LEVEL {self.level}"
            if not self.over or math.floor(self.clock * 4) % 2:
                draw_tiny(frame, label, 64 - tiny_width(label) // 2, 20, (255, 214, 70))


# --- Invaders -------------------------------------------------------------------

ALIENS = (
    (("..o.o..", ".ooooo.", "oo.o.oo", "ooooooo", "o.o.o.o"),
     ("..o.o..", ".ooooo.", "oo.o.oo", "ooooooo", ".o...o.")),
    ((".o...o.", "..ooo..", ".oo.oo.", "ooooooo", "o.o.o.o"),
     (".o...o.", "o.ooo.o", "ooo.ooo", "ooooooo", ".o...o.")),
    (("..ooo..", ".ooooo.", "oo.o.oo", ".ooooo.", "o.o.o.o"),
     ("..ooo..", ".ooooo.", "oo.o.oo", ".ooooo.", ".o.o.o.")),
)
ALIEN_COLORS = ((255, 90, 220), (80, 220, 255), (120, 255, 120))
CANNON = ("...o...", ".ooooo.", "ooooooo")
SAUCER = ("..rrrrr..", ".rwrwrwr.", "rrrrrrrrr")


class Invaders(Game):
    title = "INVADERS"

    def reset(self):
        self.wave, self.score, self.lives = 0, 0, 3
        self.new_wave()

    def new_wave(self):
        self.aliens = {(c, r) for c in range(8) for r in range(3)}
        self.ox, self.oy, self.dx = 12.0, 2.0 + min(self.wave, 2), 2
        self.march, self.frame = 0.0, 0
        self.player, self.shot, self.bombs = 60.0, None, []
        self.bomb_timer, self.saucer, self.saucer_timer = 1.2, None, self.rng.uniform(5, 11)
        self.dying = 0.0

    def _box(self, column, row):
        return self.ox + column * 12, self.oy + row * 7

    def tick(self, dt):
        if self.dying:
            self.dying += dt
            if self.dying > 1.6:
                self.lives -= 1
                if self.lives <= 0:
                    self.restart()
                else:
                    self.dying, self.bombs, self.shot = 0.0, [], None
            return
        self.march -= dt
        if self.march <= 0 and self.aliens:
            self.march = max(.06, .5 * len(self.aliens) / 24)
            self.frame ^= 1
            xs = [self._box(c, r)[0] for c, r in self.aliens]
            if (self.dx > 0 and max(xs) + 7 + self.dx > 127) or (self.dx < 0 and min(xs) + self.dx < 0):
                self.oy += 2
                self.dx = -self.dx
            else:
                self.ox += self.dx
            if max(self._box(c, r)[1] for c, r in self.aliens) + 5 >= 27:
                self.lives = 1
                self._hit_player()
                return
        lowest = {}
        for column, row in self.aliens:
            lowest[column] = max(row, lowest.get(column, -1))
        center = self.player + 3
        target = center
        if lowest:
            column = min(lowest, key=lambda c: abs(self._box(c, 0)[0] + 3 - center) + self.rng.random() * .1)
            target = self._box(column, 0)[0] + 3
        for bx, by in self.bombs:
            if by > 14 and abs(bx - center) < 5:
                target = center + (8 if bx <= center else -8)
        self.player = min(120.0, max(0.0, self.player + max(-48 * dt, min(48 * dt, target - center))))
        if self.shot is None and abs(target - center) < 2:
            self.shot = [self.player + 3, 26.0]
        if self.shot:
            self.shot[1] -= 95 * dt
            for column, row in list(self.aliens):
                x, y = self._box(column, row)
                if x <= self.shot[0] <= x + 6 and y <= self.shot[1] <= y + 5:
                    self.aliens.discard((column, row))
                    self.score += (3 - row) * 10
                    self.burst(x + 3, y + 2, (ALIEN_COLORS[row], WHITE), 12)
                    self.shot = None
                    break
            if self.shot and self.saucer and abs(self.shot[0] - self.saucer[0] - 4) < 5 and self.shot[1] < 3:
                self.score += 100
                self.burst(self.saucer[0] + 4, 1, ((255, 60, 60), WHITE), 20, 50)
                self.saucer, self.shot = None, None
            if self.shot and self.shot[1] < -3:
                self.shot = None
        self.bomb_timer -= dt
        if self.bomb_timer <= 0 and lowest:
            column = self.rng.choice(list(lowest))
            x, y = self._box(column, lowest[column])
            self.bombs.append([x + 3, y + 6])
            self.bomb_timer = self.rng.uniform(.45, 1.1)
        for bomb in self.bombs:
            bomb[1] += 42 * dt
            if self.player <= bomb[0] <= self.player + 6 and 28 <= bomb[1] <= 31:
                self._hit_player()
                return
        self.bombs = [bomb for bomb in self.bombs if bomb[1] < 32]
        if self.saucer is None:
            self.saucer_timer -= dt
            if self.saucer_timer <= 0:
                self.saucer = [-9.0, 30.0] if self.rng.random() < .5 else [128.0, -30.0]
        else:
            self.saucer[0] += self.saucer[1] * dt
            if not -10 <= self.saucer[0] <= 129:
                self.saucer, self.saucer_timer = None, self.rng.uniform(8, 14)
        if not self.aliens:
            self.wave += 1
            self.rounds += 1  # A cleared wave is a natural moment to move on.
            self.new_wave()

    def _hit_player(self):
        self.dying = 1e-6
        self.burst(self.player + 3, 29, ((120, 255, 120), (255, 214, 70), WHITE), 30, 50)

    def draw(self, frame):
        for column, row in self.aliens:
            x, y = self._box(column, row)
            stamp(frame, sprite(ALIENS[row][self.frame], {"o": ALIEN_COLORS[row]}), x, y)
        if not self.dying:
            stamp(frame, sprite(CANNON, {"o": (120, 255, 120)}), self.player, 28)
        draw = ImageDraw.Draw(frame)
        if self.shot:
            draw.line((round(self.shot[0]), round(self.shot[1]), round(self.shot[0]), round(self.shot[1]) + 2), fill=WHITE)
        for bx, by in self.bombs:
            x, y = round(bx), round(by)
            draw.point((x + (math.floor(self.clock * 10) % 2), y), fill=(255, 214, 70))
            draw.point((x, y + 1), fill=(255, 214, 70))
        if self.saucer:
            stamp(frame, sprite(SAUCER, {"r": (255, 60, 60), "w": WHITE}), self.saucer[0], 0)
        for life in range(max(0, self.lives - 1)):
            draw.line((1 + life * 4, 31, 3 + life * 4, 31), fill=(120, 255, 120))


# --- Pong -----------------------------------------------------------------------

class Pong(Game):
    title = "PONG"
    gravity = 0.0

    def reset(self):
        self.scores, self.over = [0, 0], 0.0
        self.paddles = [12.0, 12.0]
        self.serve(self.rng.choice((-1, 1)))

    def serve(self, direction):
        self.ball = [63.0, self.rng.uniform(8, 22)]
        speed, angle = 55.0, self.rng.uniform(-.55, .55)
        self.vx, self.vy = direction * speed * math.cos(angle), speed * math.sin(angle)
        self.wait = .8
        self.errors = [self.rng.uniform(-3, 3), self.rng.uniform(-3, 3)]

    def _predict(self, x_target):
        x, y = self.ball
        if self.vx == 0:
            return y
        py = y + self.vy * (x_target - x) / self.vx
        mirrored = py % 60
        return mirrored if mirrored <= 30 else 60 - mirrored

    def tick(self, dt):
        if self.over:
            self.over += dt
            if self.over > 2:
                self.restart()
            return
        for side, x_face in ((0, 4), (1, 122)):
            toward = (self.vx < 0) if side == 0 else (self.vx > 0)
            target = (self._predict(x_face) + 1 - 4 + self.errors[side]) if toward else 12
            self.paddles[side] += max(-40 * dt, min(40 * dt, target - self.paddles[side]))
            self.paddles[side] = min(24.0, max(0.0, self.paddles[side]))
        if self.wait > 0:
            self.wait -= dt
            return
        ball = self.ball
        ball[0] += self.vx * dt
        ball[1] += self.vy * dt
        if ball[1] < 0 or ball[1] > 30:
            ball[1] = min(30.0, max(0.0, ball[1]))
            self.vy = -self.vy
        for side, face in ((0, 4), (1, 122)):
            moving = self.vx < 0 if side == 0 else self.vx > 0
            touching = ball[0] <= face if side == 0 else ball[0] + 2 >= face + 2
            if moving and touching and self.paddles[side] - 2 <= ball[1] <= self.paddles[side] + 8:
                speed = min(140.0, math.hypot(self.vx, self.vy) * 1.07)
                offset = (ball[1] + 1 - (self.paddles[side] + 4)) / 5
                self.vy = speed * max(-.7, min(.7, offset * .6))
                self.vx = math.copysign(math.sqrt(speed * speed - self.vy * self.vy), 1 if side == 0 else -1)
                ball[0] = face if side == 0 else face
                self.errors[side] = self.rng.uniform(-3, 3) if self.rng.random() < .72 else self.rng.choice((-12, 12))
                self.burst(ball[0] + 1, ball[1] + 1, (WHITE,), 5, 25)
        if ball[0] < -3 or ball[0] > 129:
            winner = 1 if ball[0] < 0 else 0
            self.scores[winner] += 1
            self.burst(0 if winner else 127, ball[1], ((255, 214, 70), WHITE), 18, 45)
            if max(self.scores) >= 5:
                self.over = 1e-6
            self.serve(-1 if winner else 1)

    def draw(self, frame):
        draw = ImageDraw.Draw(frame)
        for y in range(0, 32, 4):
            draw.rectangle((63, y, 64, y + 1), fill=(50, 50, 60))
        left, right = str(self.scores[0]), str(self.scores[1])
        draw_text(frame, left, 58 - text_width(left), 1, (110, 110, 130))
        draw_text(frame, right, 70, 1, (110, 110, 130))
        for x, y in ((2, self.paddles[0]), (124, self.paddles[1])):
            draw.rectangle((x, round(y), x + 1, round(y) + 7), fill=WHITE)
        if not self.over and self.wait <= 0 or math.floor(self.clock * 6) % 2:
            x, y = round(self.ball[0]), round(self.ball[1])
            draw.rectangle((x, y, x + 1, y + 1), fill=WHITE)
        if self.over:
            winner = "LEFT WINS" if self.scores[0] > self.scores[1] else "RIGHT WINS"
            draw_tiny(frame, winner, 64 - tiny_width(winner) // 2, 22, (255, 214, 70))


# --- Pixel Quest: a procedurally generated side-scroller -----------------------

TILE = 4
QUEST_THEMES = (
    # Muted skies and hills keep the hero, coins and beetles the brightest things on screen.
    {"sky": ((6, 20, 56), (26, 62, 110)), "hill": (20, 50, 44), "ground": (118, 74, 42),
     "grass": (86, 200, 90), "block": (196, 118, 58), "stars": False},
    {"sky": ((40, 16, 40), (120, 60, 46)), "hill": (60, 32, 46), "ground": (104, 62, 44),
     "grass": (230, 170, 60), "block": (186, 90, 60), "stars": False},
    {"sky": ((2, 4, 22), (18, 26, 70)), "hill": (16, 30, 60), "ground": (62, 54, 84),
     "grass": (110, 124, 230), "block": (122, 112, 176), "stars": True},
    {"sky": ((6, 4, 10), (34, 20, 26)), "hill": (46, 28, 32), "ground": (96, 58, 40),
     "grass": (184, 110, 60), "block": (150, 150, 162), "stars": False},
)
HERO = {"r": (255, 76, 60), "w": (255, 255, 255), "k": (20, 20, 40), "y": (255, 220, 60)}
HERO_RUN = (("..y..", ".rrr.", "rwkwr", ".rrr.", "rrrrr", ".r.r.", "r...r"),
            ("..y..", ".rrr.", "rwkwr", ".rrr.", "rrrrr", ".r.r.", ".r.r."))
HERO_JUMP = ("..y..", ".rrr.", "rwkwr", "rrrrr", ".rrr.", "r...r", ".....")
BEETLE = (("..p..", ".ppp.", "pwpwp", "p.p.p"), ("..p..", ".ppp.", "pwpwp", ".p.p."))


@lru_cache(maxsize=4)
def quest_sky(index):
    theme = QUEST_THEMES[index]
    sky = Image.new("RGB", (128, 32))
    draw = ImageDraw.Draw(sky)
    for y in range(32):
        draw.line((0, y, 127, y), fill=mix(theme["sky"][0], theme["sky"][1], y / 31))
    if theme["stars"]:
        rng = random.Random(index)
        for _ in range(26):
            sky.putpixel((rng.randrange(128), rng.randrange(18)), (200, 210, 255))
    return sky


HILL_PERIOD = 704  # Both hill waves repeat exactly over this width, so the strip tiles seamlessly.


@lru_cache(maxsize=4)
def quest_hills(index):
    """Parallax hills pre-rendered once per world; each frame pastes a slice."""
    strip = Image.new("RGBA", (HILL_PERIOD + 128, 32))
    draw = ImageDraw.Draw(strip)
    for x in range(HILL_PERIOD + 128):
        height = 8 + round(3 * math.sin(math.tau * x / 88) + 2 * math.sin(math.tau * x / 35.2))
        draw.line((x, 32 - height - 4, x, 31), fill=(*QUEST_THEMES[index]["hill"], 255))
    return strip


class Platformer(Game):
    title = "PIXEL QUEST"
    gravity = 60.0
    W, H = 5, 7

    def reset(self):
        self.world, self.lives, self.score, self.coin_count = 0, 3, 0, 0
        self.new_level()

    def new_level(self):
        self.theme = QUEST_THEMES[self.world % len(QUEST_THEMES)]
        self.columns, self.coins, self.enemies = [], set(), []
        self.length = 140 + self.world * 20
        self.ground, self.after_gap = 2, True
        self.x, self.y, self.vy = 8.0, 32 - 2 * TILE - self.H, 0.0
        self.camera, self.checkpoint = 0.0, 2
        self.cleared = self.dead = self.safe = 0.0
        self.bias = 1.0

    # Level generation: columns are produced on demand as the camera advances.
    def column(self, index):
        while len(self.columns) <= index and len(self.columns) < self.length + 40:
            self._generate()
        if index >= len(self.columns):
            return {"ground": 2, "platform": None}
        return self.columns[max(0, index)]

    def _generate(self):
        index, rng = len(self.columns), self.rng
        if index < 10 or index >= self.length - 4:
            return self._flat(12)
        roll = rng.random()
        if roll < .22 and not self.after_gap:
            self._gap(rng.randint(2, 3))
        elif roll < .42:
            self.ground = max(1, min(3, self.ground + rng.choice((-1, 1))))
            self._flat(rng.randint(3, 6))
        elif roll < .64:
            self._flat(rng.randint(6, 9), platform=True)
        else:
            self._flat(rng.randint(5, 9), enemy=rng.random() < .6)

    def _flat(self, count, platform=False, enemy=False):
        start = len(self.columns)
        self.columns.extend({"ground": self.ground, "platform": None} for _ in range(count))
        top_row = 8 - self.ground
        if platform:
            row = top_row - 3
            # Two open columns before the segment ends: the hero is wider than one, and a
            # platform right up to a step leaves no headroom to jump it (stuck for good).
            for column in range(start + 1, start + min(count - 2, 5)):
                self.columns[column]["platform"] = row
                self.coins.add((column, row - 1))
        elif self.rng.random() < .2:
            for column in range(start + 2, start + count - 1, 3):
                self.coins.add((column, top_row - 2))
        if enemy:
            self.enemies.append([float((start + count // 2) * TILE), float(top_row * TILE - 4),
                                 self.rng.choice((-9.0, 9.0)), True])
        self.after_gap = False

    def _gap(self, count):
        start = len(self.columns)
        self.columns.extend({"ground": 0, "platform": None} for _ in range(count))
        for offset in range(count):
            self.coins.add((start + offset, 8 - self.ground - 4))
        self.after_gap = True

    def solid(self, px, py):
        if py < 0 or py >= 32:
            return False
        column = self.column(int(px // TILE))
        row = int(py // TILE)
        return bool(column["ground"] and row >= 8 - column["ground"]) or column["platform"] == row

    def _grounded(self):
        foot = self.y + self.H + .5
        return self.solid(self.x + 1, foot) or self.solid(self.x + self.W - 1, foot)

    def _should_jump(self):
        ahead, foot = self.x + self.W, self.y + self.H + 1
        for distance in range(1, 9):
            probe = ahead + distance
            if not any(self.solid(probe, foot + drop) for drop in (0, 4, 8)):
                return distance <= self.bias
        if self.solid(ahead + 3, self.y + self.H - 2):
            return True
        for enemy in self.enemies:
            gap = enemy[0] - ahead
            if enemy[3] and 0 <= gap <= 10 and abs(enemy[1] + 4 - (self.y + self.H)) < 3:
                return gap <= 5 + self.bias
        return self.rng.random() < .003

    def _die(self):
        self.lives -= 1
        self.dead = 1e-6
        self.burst(self.x - self.camera + 2, min(29, self.y + 3), (HERO["r"], HERO["y"], WHITE), 18, 40)

    def tick(self, dt):
        if self.cleared:
            self.cleared += dt
            if self.cleared > 2.6:
                self.world += 1
                self.new_level()
            return
        if self.dead:
            self.dead += dt
            if self.dead > 1.4:
                if self.lives <= 0:
                    self.restart()
                    return
                self.dead = 0.0
                # Come back on solid ground a few steps short of the edge, with a
                # fresh read of the jump: keeping the misjudged one walked the hero
                # straight back into the same pit until every life was gone.
                start = self.checkpoint
                while start > 2 and not all(self.column(start + step)["ground"] for step in range(3)):
                    start -= 1
                start = max(2, start - 2)
                while start > 2 and not self.column(start)["ground"]:
                    start -= 1
                self.x = start * TILE + 1.0
                self.y = 32 - self.column(start)["ground"] * TILE - self.H - 1
                self.vy = 0.0
                self.bias = 2.5
                self.safe = 1.5  # A moment of grace, blinking, like any platformer.
            return
        if self._grounded():
            here = int((self.x + 2) // TILE)
            if self.column(here)["ground"]:
                self.checkpoint = here
            if self._should_jump():
                self.vy = -98.0
                # Usually jumps at the edge; now and then it misjudges a gap.
                self.bias = -1.0 if self.rng.random() < .09 else self.rng.uniform(1.5, 3.5)
        forward = self.x + 26 * dt
        if not (self.solid(forward + self.W, self.y + 1) or self.solid(forward + self.W, self.y + self.H - 1)):
            self.x = forward
        self.vy = min(160.0, self.vy + 280 * dt)
        next_y = self.y + self.vy * dt
        if self.vy >= 0 and (self.solid(self.x + 1, next_y + self.H) or self.solid(self.x + self.W - 1, next_y + self.H)):
            next_y, self.vy = int((next_y + self.H) // TILE) * TILE - self.H, 0.0
        elif self.vy < 0 and (self.solid(self.x + 1, next_y) or self.solid(self.x + self.W - 1, next_y)):
            next_y, self.vy = (int(next_y // TILE) + 1) * TILE, 0.0
        self.y = next_y
        if self.y > 34:
            self._die()
            return
        for column in {int(self.x // TILE), int((self.x + self.W) // TILE)}:
            for row in range(int(self.y // TILE), int((self.y + self.H) // TILE) + 1):
                if (column, row) in self.coins:
                    self.coins.discard((column, row))
                    self.score += 10
                    self.coin_count += 1
                    self.burst(column * TILE + 2 - self.camera, row * TILE + 2, (HERO["y"], WHITE), 3, 18)
        self.safe = max(0.0, self.safe - dt)
        for enemy in self.enemies:
            if not enemy[3] or abs(enemy[0] - self.x) > 140:
                continue
            step = enemy[0] + enemy[2] * dt
            front = step + (5 if enemy[2] > 0 else 0)
            if self.solid(front, enemy[1] + 2) or not self.solid(front, enemy[1] + 5):
                enemy[2] = -enemy[2]
            else:
                enemy[0] = step
            if enemy[0] < self.x + self.W and enemy[0] + 5 > self.x and enemy[1] < self.y + self.H and enemy[1] + 4 > self.y:
                if self.vy > 0 and self.y + self.H - enemy[1] < 4:
                    enemy[3] = False
                    self.vy = -72.0
                    self.score += 100
                    self.burst(enemy[0] - self.camera + 2, enemy[1] + 2, ((170, 80, 220), WHITE), 12)
                elif not self.safe:
                    self._die()
                    return
        if self.x >= self.length * TILE:
            self.cleared = 1e-6
            self.score += 1000
            for n in range(4):
                self.burst(self.rng.randrange(20, 108), self.rng.randrange(4, 16), (hsv(n / 4), WHITE), 16, 45)
        target = max(0.0, self.x - 40)
        self.camera += (target - self.camera) * min(1.0, dt * 8)

    def draw(self, frame):
        theme = self.theme
        frame.paste(quest_sky(self.world % len(QUEST_THEMES)))
        draw = ImageDraw.Draw(frame)
        camera = round(self.camera)
        offset = (camera // 3) % HILL_PERIOD
        hills = quest_hills(self.world % len(QUEST_THEMES)).crop((offset, 0, offset + 128, 32))
        frame.paste(hills, (0, 0), hills)
        first = camera // TILE
        for index in range(first, first + 34):
            column, x = self.column(index), index * TILE - camera
            if column["ground"]:
                top = 32 - column["ground"] * TILE
                draw.rectangle((x, top, x + 3, 31), fill=theme["ground"])
                draw.line((x, top, x + 3, top), fill=theme["grass"])
            if column["platform"] is not None:
                y = column["platform"] * TILE
                draw.rectangle((x, y, x + 3, y + 3), fill=theme["block"])
                draw.line((x, y, x + 2, y), fill=mix(theme["block"], (255, 255, 255), .4))
                draw.line((x + 3, y, x + 3, y + 3), fill=dim(theme["block"], .6))
        shine = math.floor(self.clock * 4) % 2
        for column, row in self.coins:
            if first - 1 <= column <= first + 33:
                x, y = column * TILE - camera + 1, row * TILE + 1
                draw.rectangle((x, y, x + 1, y + 1), fill=HERO["y"])
                if shine:
                    draw.point((x, y), fill=WHITE)
        flag = self.length * TILE - camera
        if -6 < flag < 128:
            base = 32 - self.column(self.length)["ground"] * TILE
            draw.line((flag, base - 18, flag, base - 1), fill=(220, 220, 220))
            draw.polygon(((flag + 1, base - 18), (flag + 6, base - 15), (flag + 1, base - 12)), fill=(80, 230, 120))
        for x, y, speed, alive in self.enemies:
            if alive and -6 < x - camera < 128:
                stamp(frame, sprite(BEETLE[math.floor(self.clock * 6) % 2], {"p": (170, 80, 220), "w": WHITE}),
                      x - camera, y)
        if (not self.dead or math.floor(self.dead * 12) % 2) and not (self.safe and math.floor(self.safe * 10) % 2):
            art = HERO_JUMP if not self._grounded() else HERO_RUN[math.floor(self.clock * 10) % 2]
            stamp(frame, sprite(art, HERO), self.x - camera, self.y)
        draw_tiny(frame, f"{self.score:05d}", 2, 1, WHITE)
        world = f"W{self.world + 1} {'*' * max(0, self.lives)}"
        draw_tiny(frame, world, 126 - tiny_width(world), 1, WHITE)
        if self.cleared and math.floor(self.clock * 4) % 2:
            label = "COURSE CLEAR"
            draw_tiny(frame, label, 64 - tiny_width(label) // 2, 12, HERO["y"])


GAMES = {"quest": Platformer, "tetris": Tetris, "snake": Snake, "breakout": Breakout,
         "invaders": Invaders, "pong": Pong}


OUTRO_SECONDS = 1.2


class Arcade(Module):
    name = "arcade"

    def __init__(self):
        self.rng = random.Random()
        self.game = None
        self.current = None
        self.title = None
        self.last_t = None
        self.started = 0.0
        self.accumulator = 0.0
        self.scene = None
        self.outro = None  # (started_at, last frame) while a finished game fades out

    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def _start(self, settings, t):
        mode = settings["mode"]
        self.current = mode if mode in GAMES else self.rng.choice([g for g in GAMES if g != self.current])
        self.game = GAMES[self.current](random.Random(self.rng.getrandbits(32)))
        self.title = Lettering(self.game.title, self.rng.choice(("drop", "slot", "assemble", "split")),
                               ((255, 214, 70), (255, 90, 40), (80, 200, 255)),
                               self.rng.choice(("alternate", "chase")), self.rng.getrandbits(30), hold=.2)
        self.started, self.accumulator, self.outro = t, 0.0, None

    def hold(self, context):
        # Keep the playlist here while a round is in play; let it go once the
        # round has ended and the game is fading out.
        return self.game is not None and self.outro is None

    def render(self, context):
        settings = context.config["plugins"][self.name]
        t = context.animation_time
        if self.game is None or self.last_t is None or t < self.last_t - 1e-6 or context.scene != self.scene:
            self._start(settings, t)
            self.last_t = t
        self.scene = context.scene
        dt = min(.1, max(0.0, t - self.last_t))
        self.last_t = t
        local = t - self.started
        played = local - self.title.duration
        if settings["mode"] == "auto" and self.outro is None and (
                (self.game.rounds and played >= settings["scene_seconds"])
                or played >= settings["scene_seconds"] * 3):
            # Move on only at a natural break (or a generous cap), never mid-play.
            self.outro = (t, self._frame())
        if self.outro is not None:
            fade = (t - self.outro[0]) / OUTRO_SECONDS
            if fade < 1:
                return Image.blend(self.outro[1], new_frame(), ease_in_out(fade))
            self._start(settings, t)
            local = 0.0
        frame = new_frame()
        if local < self.title.duration:
            self.title.draw(frame, local)
            return frame
        self.accumulator += dt
        steps = 0
        while self.accumulator >= STEP and steps < 8:
            self.game.update(STEP)
            self.accumulator -= STEP
            steps += 1
        if steps == 8:
            self.accumulator = 0.0
        return self._frame()

    def _frame(self):
        frame = new_frame()
        self.game.draw(frame)
        self.game.particles.draw(frame)
        return frame


def migrate(settings):
    if settings.get("mode") in ("runner", "platform", "shuffle", "life"):
        settings["mode"] = "auto"  # Retired scenes: canned runners, and Life, which read as noise.
    return settings


def validate(settings):
    if settings.get("mode") not in ("auto", *GAMES):
        raise ValueError(f"mode must be auto or one of {', '.join(GAMES)}")
    value = settings.get("scene_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 8 <= value <= 180:
        raise ValueError("scene_seconds must be 8–180")


plugin = Plugin("arcade", "Arcade", module=Arcade,
                defaults={"mode": "auto", "scene_seconds": 30},
                validate_settings=validate, migrate_settings=migrate,
                choices={"mode": ("auto", *GAMES)},
                help={"mode": "auto rotates through every game",
                      "scene_seconds": "Seconds per game in auto mode (8–180)"},
    ui={"scene_seconds": {"type": "slider", "min": 8, "max": 180, "unit": "s", "label": "Seconds per game"}})
