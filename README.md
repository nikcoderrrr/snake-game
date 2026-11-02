# 🐍 Snake Game

A feature-rich Snake Game built using Python, featuring multiple difficulty levels, bonus food, obstacles, high-score tracking, and customizable gameplay settings.

## 🎮 Features

- **Three Difficulty Levels:** Easy, Normal, and Hard.
- **Dynamic Speed:** The snake gradually becomes faster as it grows.
- **Bonus Food:** Collect bonus items for extra points before they disappear.
- **Obstacles:** Additional obstacles appear as you progress through levels.
- **Wrap-Around Mode:** Pass through one wall and emerge from the opposite side.
- **High Score Tracking:** Saves high scores separately for different game settings.
- **Pause and Resume:** Pause or resume gameplay whenever needed.
- **Game Over Animation:** Visual blinking effect when the snake dies.
- **Keyboard Controls:** Supports both arrow keys and WASD.

## 🛠️ Technologies Used

- Python 3.9+
- Turtle Graphics
- Tkinter
- JSON
- Random
- Dataclasses and Enums

## 📋 Prerequisites

Make sure you have Python 3.9 or later installed.

Tkinter is included with most standard Python installations.

## 🚀 How to Run

1. Clone this repository:

   ```bash
   git clone https://github.com/YOUR_USERNAME/snake-game.git
   ```

2. Navigate to the project directory:

   ```bash
   cd snake-game
   ```

3. Run the game:

   ```bash
   python snake.py
   ```

## 🎯 Controls

| Key | Action |
|---|---|
| Arrow Keys / WASD | Move the snake |
| SPACE | Start / Pause / Resume |
| R | Restart the game |
| M | Return to the menu |
| ESC | Quit the game |
| 1 / 2 / 3 | Select difficulty |
| T | Toggle wrap-around mode |
| O | Toggle obstacles |

*Difficulty and gameplay settings can only be changed from the main menu.*

## 📂 Project Structure

```text
snake-game/
│
├── snake.py                # Main game implementation
├── README.md               # Project documentation
├── .gitignore              # Files excluded from Git
└── snake_highscore.json    # Generated high-score data
```

## 🧠 Implementation Highlights

- **Immutable Game State:** Uses frozen dataclasses to prevent accidental state mutations.
- **Separation of Concerns:** Game rules, rendering, and input handling are organized separately.
- **Pure Game Logic:** Core game rules are independent of the graphical interface.
- **Flood Fill Algorithm:** Used to ensure obstacle placement does not unfairly block accessible areas.
- **Input Queue:** Prevents invalid consecutive turns and accidental self-collisions.
- **Persistent High Scores:** Stores scores in JSON files.

## 👩‍💻 Author

Developed as a Python programming project.

---

Enjoy playing! 🐍