"""Выбор области основного экрана и сохранение изображения в PNG."""
import os
import tempfile
from PIL import ImageGrab


def crop_selection(image, start, end, display_size):
    """Перевод координат окна выбора в пиксели снимка (в том числе при DPI scaling)."""
    width, height = display_size
    x1, x2 = sorted((max(0, min(width, start[0])), max(0, min(width, end[0]))))
    y1, y2 = sorted((max(0, min(height, start[1])), max(0, min(height, end[1]))))
    if x1 == x2 or y1 == y2:
        return None
    box = (int(x1 * image.width / width), int(y1 * image.height / height),
           int(x2 * image.width / width), int(y2 * image.height / height))
    return image.crop(box)


def select_area(root, on_complete, on_error):
    """Скрыть окно, дать выбрать прямоугольник; Esc отменяет выбор."""
    import tkinter as tk
    from PIL import ImageTk

    root.withdraw()

    def begin():
        overlay = None
        try:
            image = ImageGrab.grab()
            width, height = root.winfo_screenwidth(), root.winfo_screenheight()
            overlay = tk.Toplevel(root)
            overlay.overrideredirect(True)
            overlay.geometry(f"{width}x{height}+0+0")
            overlay.attributes("-topmost", True)
            canvas = tk.Canvas(overlay, width=width, height=height, highlightthickness=0, cursor="crosshair")
            canvas.pack(fill="both", expand=True)
            photo = ImageTk.PhotoImage(image.resize((width, height)))
            canvas.create_image(0, 0, image=photo, anchor="nw")
            canvas.image = photo
            canvas.create_text(width // 2, 24, text="Выделите область мышью. Esc — отмена",
                               fill="white", font=("Segoe UI", 14, "bold"))
            start = [0, 0]
            rectangle = [None]

            def finish(path=None):
                overlay.destroy()
                root.deiconify()
                root.lift()
                on_complete(path)

            def down(event):
                start[:] = [event.x, event.y]
                if rectangle[0] is not None:
                    canvas.delete(rectangle[0])
                rectangle[0] = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                       outline="#ff3434", width=2)

            def drag(event):
                if rectangle[0] is not None:
                    canvas.coords(rectangle[0], *start, event.x, event.y)

            def release(event):
                try:
                    selected = crop_selection(image, start, (event.x, event.y), (width, height))
                    if selected is None:
                        return
                    fd, path = tempfile.mkstemp(prefix="csl_shot_", suffix=".png")
                    os.close(fd)
                    try:
                        selected.save(path, "PNG")
                    except Exception:
                        os.unlink(path)
                        raise
                    finish(path)
                except Exception as exc:
                    overlay.destroy()
                    root.deiconify()
                    root.lift()
                    on_error(exc)

            canvas.bind("<ButtonPress-1>", down)
            canvas.bind("<B1-Motion>", drag)
            canvas.bind("<ButtonRelease-1>", release)
            overlay.bind("<Escape>", lambda event: finish())
            overlay.focus_force()
        except Exception as exc:
            if overlay is not None:
                overlay.destroy()
            root.deiconify()
            root.lift()
            on_error(exc)

    # Дать Windows время убрать окно с экрана до захвата.
    root.after(250, begin)
