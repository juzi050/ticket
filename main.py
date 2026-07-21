from __future__ import annotations

import tkinter as tk

from app.gui.mvp_application import MvpApplication


def main() -> None:
    root = tk.Tk()
    MvpApplication(root)
    root.mainloop()


if __name__ == "__main__":
    main()
