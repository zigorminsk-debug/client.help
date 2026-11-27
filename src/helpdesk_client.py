#!/usr/bin/env python3
"""
helpdesk_client.py — графическое приложение «Заявка в техподдержку» (CSL Helpdesk).

Запускается ярлыком, который разворачивается всем пользователям через GPO.
При первом запуске один раз запрашивает контактный телефон (сохраняется в профиле
пользователя и может быть изменён). Собирает сведения о ПК, позволяет отметить
неисправности, приложить скриншот и голосовой комментарий, после чего отправляет
заявку на Jira (h.csl.by) под единым логином организации. При наличии настроенного
репозитория GitHub самообновляется при выходе новой версии.

Сборка в EXE:  pyinstaller --onefile --windowed --name CSLHelpdesk src/helpdesk_client.py
"""

import os
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, ttk

# Гарантируем видимость соседних модулей (при запуске как скрипта и в EXE).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _version
import config as cfg
import system_info as sysinfo
import jira_client as jira
import screenshot as shot
import audio_recorder as audio
import updater as updater

APP_TITLE = cfg.APP_TITLE
APP_VERSION = getattr(_version, "APP_VERSION", "0")
BUILD_DATE = getattr(_version, "BUILD_DATE", "")


class HelpdeskApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_TITLE} — v{APP_VERSION}")
        self.root.resizable(True, True)

        # Иконка приложения (жёлтый треугольник с восклицательным знаком) —
        # одинаковая для окна, ярлыков и задачи (EXE/MSI наследуют её из PNG).
        icon = self._icon_path()
        if icon:
            try:
                self._app_icon = tk.PhotoImage(file=icon)
                self.root.iconphoto(True, self._app_icon)
            except Exception:
                pass

        self._apply_style()

        self.machine_cfg = cfg.load_machine_config()
        self.user_data = cfg.load_user_data()

        self.screenshot_path = None
        self.voice_path = None
        self.recorder = None
        self.recording = False

        self._pending_update = None  # (download_url, token)

        self._build_ui()
        self._collect_system_info()

        if not self.user_data.get("phone"):
            self._prompt_phone()

        if not cfg.is_configured():
            messagebox.showwarning(
                "Не настроена групповая политика",
                "Приложение запущено, но не найдены настройки подключения к Jira.\n"
                "Обратитесь к администратору: ему нужно один раз настроить GPO "
                "(указать адрес h.csl.by, единый логин/пароль и ключ проекта)."
            )

        # Автопроверка обновлений (только для реальных сборок, не для локальной разработки)
        if self.machine_cfg.get("update_enabled") and APP_VERSION not in ("0", ""):
            self.root.after(2500, self._auto_check_update)

    # ------------------------------------------------------------------ UI
    def _icon_path(self):
        """Путь к PNG-иконке (с учётом распаковки PyInstaller --onefile)."""
        try:
            base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        except Exception:
            base = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(base, "assets", "icon.png")
        return p if os.path.exists(p) else None

    def _header_icon(self):
        """Маленькая иконка для шапки (ресайз через PIL, если доступна)."""
        try:
            from PIL import Image
            import io
            path = self._icon_path()
            if not path:
                return None
            im = Image.open(path).convert("RGBA").resize((36, 36), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            return tk.PhotoImage(data=buf.getvalue())
        except Exception:
            return None

    def _apply_style(self):
        """Профессиональная палитра и тема (clam) для tkinter/ttk."""
        try:
            style = ttk.Style()
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            return
        BG = "#f4f5f7"
        SURFACE = "#ffffff"
        ACCENT = "#f5b800"
        ACCENT_D = "#d99e00"
        FG = "#1f2733"
        MUTED = "#6b7280"
        s = style
        s.configure(".", font=("Segoe UI", 10), background=BG, foreground=FG)
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=SURFACE)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Card.TLabel", background=SURFACE, foreground=FG)
        s.configure("Card.Sub.TLabel", background=SURFACE, foreground=MUTED)
        s.configure("Card.Header.TLabel", background=SURFACE, foreground=FG,
                    font=("Segoe UI", 16, "bold"))
        s.configure("TLabelframe", background=BG, bordercolor="#d9dde3", relief="solid")
        s.configure("TLabelframe.Label", background=BG, foreground=MUTED,
                    font=("Segoe UI", 10, "bold"))
        s.configure("Card.TLabelframe", background=SURFACE, bordercolor="#e3e6ea")
        s.configure("Card.TLabelframe.Label", background=SURFACE, foreground=MUTED,
                    font=("Segoe UI", 10, "bold"))
        s.configure("TButton", padding=(14, 6), font=("Segoe UI", 10))
        s.configure("Card.TButton", background=SURFACE)
        s.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=(18, 8))
        s.configure("TCheckbutton", background=BG, focuscolor=BG, padding=(4, 2))
        s.configure("Card.TCheckbutton", background=SURFACE, focuscolor=SURFACE)
        s.configure("TEntry", fieldbackground=SURFACE, padding=(8, 5))
        s.configure("TText", fieldbackground=SURFACE, padding=(8, 5))
        s.configure("Status.TLabel", background="#eaeef3", foreground=MUTED, padding=(10, 6))
        s.map("Accent.TButton",
              background=[("active", ACCENT_D), ("!disabled", ACCENT)],
              foreground=[("!disabled", "#1a1a1a")])
        s.map("Card.TButton",
              background=[("active", "#eef0f4"), ("!disabled", SURFACE)],
              foreground=[("!disabled", FG)])

    def _build_ui(self):
        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=14, pady=12)

        # --- Шапка с иконкой ---
        header = ttk.Frame(main, style="Card.TFrame")
        header.pack(fill="x", pady=(0, 10))
        ico = self._header_icon()
        if ico:
            self._header_img = ico
            ttk.Label(header, image=ico, style="Card.TLabel").pack(
                side="left", padx=(14, 12), pady=10)
        ttk.Label(header, text="CSL Helpdesk", style="Card.Header.TLabel").pack(
            side="left", anchor="w")
        ttk.Label(header, text=f"Заявка в техподдержку · версия {APP_VERSION}",
                  style="Card.Sub.TLabel").pack(side="left", anchor="w", pady=(0, 10))

        def card(title):
            c = ttk.LabelFrame(main, text=title, style="Card.TLabelframe")
            c.pack(fill="x", pady=6)
            return c

        # --- Контактные данные ---
        contact = card("Контактные данные")
        ttk.Label(contact, text="Телефон *", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(12, 8), pady=(10, 2))
        self.phone_var = tk.StringVar(value=self.user_data.get("phone", ""))
        self.phone_entry = ttk.Entry(contact, textvariable=self.phone_var, width=28)
        self.phone_entry.grid(row=0, column=1, columnspan=2, sticky="we",
                              padx=(0, 12), pady=(10, 2))
        self.phone_entry.bind("<FocusOut>", lambda e: self._save_phone())
        ttk.Label(contact, text="обязательный, международный формат",
                  style="Card.Sub.TLabel").grid(row=1, column=1, sticky="w", padx=(0, 12))

        ttk.Label(contact, text="AnyDesk ID", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", padx=(12, 8), pady=6)
        self.anydesk_var = tk.StringVar(
            value=self.user_data.get("anydesk") or sysinfo.get_anydesk_id())
        ttk.Entry(contact, textvariable=self.anydesk_var, width=28).grid(
            row=2, column=1, columnspan=2, sticky="we", padx=(0, 12), pady=6)

        ttk.Label(contact, text="RustDesk ID", style="Card.TLabel").grid(
            row=3, column=0, sticky="w", padx=(12, 8), pady=6)
        self.rustdesk_var = tk.StringVar(
            value=self.user_data.get("rustdesk") or sysinfo.get_rustdesk_id())
        ttk.Entry(contact, textvariable=self.rustdesk_var, width=28).grid(
            row=3, column=1, columnspan=2, sticky="we", padx=(0, 12), pady=6)

        ttk.Button(contact, text="Сохранить", command=self._save_phone).grid(
            row=4, column=0, columnspan=3, sticky="e", padx=12, pady=(4, 10))
        contact.columnconfigure(1, weight=1)
        contact.columnconfigure(2, weight=1)

        # --- Неисправности (чекбоксы, разбиты по категориям) ---
        faults = card("Что не работает? (отметьте нужное)")
        self.fault_vars = []  # (категория, label, BooleanVar)
        row = 0
        col = 0
        for group, items in self.machine_cfg.get("fault_categories", {}).items():
            ttk.Label(faults, text=group, style="Card.TLabel",
                      font=("Segoe UI", 10, "bold")).grid(
                row=row, column=0, columnspan=2, sticky="w", padx=(12, 8), pady=(8, 3))
            row += 1
            for label in items:
                v = tk.BooleanVar()
                self.fault_vars.append((group, label, v))
                ttk.Checkbutton(faults, text=label, variable=v,
                                style="Card.TCheckbutton").grid(
                    row=row, column=col, sticky="w", padx=(12, 8), pady=2)
                row += 1
                col = 1 - col
            row += 1

        # --- Комментарий ---
        cm = card("Комментарий")
        self.comment = tk.Text(cm, height=4, width=56, wrap="word")
        self.comment.grid(row=0, column=0, sticky="we", padx=10, pady=(8, 10))
        if self.user_data.get("comment"):
            self.comment.insert("1.0", self.user_data.get("comment"))

        # --- Вложения ---
        att = card("Вложения")
        self.shot_btn = ttk.Button(att, text="Сделать скриншот", command=self._take_screenshot)
        self.shot_btn.grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(10, 4))
        self.shot_label = ttk.Label(att, text="скриншот не сделан", style="Card.Sub.TLabel")
        self.shot_label.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=(10, 4))
        self.shot_clear = ttk.Button(att, text="Убрать", command=self._clear_screenshot)
        self.shot_clear.grid(row=0, column=2, sticky="e", padx=12, pady=(10, 4))

        self.voice_btn = ttk.Button(att, text="Записать голос", command=self._toggle_voice)
        self.voice_btn.grid(row=1, column=0, sticky="w", padx=(12, 8), pady=4)
        self.voice_label = ttk.Label(att, text="голосовое не записано", style="Card.Sub.TLabel")
        self.voice_label.grid(row=1, column=1, sticky="w", padx=(0, 12), pady=4)
        self.voice_clear = ttk.Button(att, text="Убрать", command=self._clear_voice)
        self.voice_clear.grid(row=1, column=2, sticky="e", padx=12, pady=4)
        if not audio.is_available():
            self.voice_btn.configure(state="disabled")
            self.voice_label.configure(text="микрофон недоступен")

        # --- Действия ---
        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=(8, 6))
        self.submit_btn = ttk.Button(actions, text="Отправить заявку",
                                     command=self._on_submit, style="Accent.TButton")
        self.submit_btn.pack(side="left", padx=(0, 8))
        self.update_btn = ttk.Button(actions, text="Проверить обновления",
                                     command=self._manual_check_update)
        if not self.machine_cfg.get("update_enabled"):
            self.update_btn.configure(state="disabled")
        self.update_btn.pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="О программе", command=self._about).pack(side="left")

        # --- Статус ---
        self.status = tk.StringVar(value=f"Готово. Версия {APP_VERSION}")
        ttk.Label(main, textvariable=self.status, style="Status.TLabel").pack(
            fill="x", pady=(4, 8))

        # --- Сведения о ПК ---
        syscard = ttk.LabelFrame(main, text="Сведения о системе (собираются автоматически)",
                                 style="Card.TLabelframe")
        syscard.pack(fill="both", expand=True, pady=6)
        self.sys_text = tk.Text(syscard, height=10, width=56, wrap="word", state="disabled")
        self.sys_text.pack(fill="both", expand=True, padx=10, pady=(8, 10))

    # ---------------------------------------------------------- system info
    def _collect_system_info(self):
        try:
            self.system_info = sysinfo.get_system_info()
        except Exception as e:
            self.system_info = {"error": str(e)}
        markup = sysinfo.format_system_info_markup(self.system_info)
        self._set_sys_text(markup)

    def _set_sys_text(self, text: str):
        self.sys_text.configure(state="normal")
        self.sys_text.delete("1.0", "end")
        self.sys_text.insert("1.0", text)
        self.sys_text.configure(state="disabled")

    # --------------------------------------------------------------- phone
    def _prompt_phone(self):
        win = tk.Toplevel(self.root)
        win.title("Контактный телефон")
        win.grab_set()
        win.resizable(False, False)
        ttk.Label(win, text="Укажите контактный телефон для связи с вами:",
                  wraplength=300).pack(padx=12, pady=10)
        var = tk.StringVar()
        entry = ttk.Entry(win, textvariable=var, width=30)
        entry.pack(padx=12, pady=4)
        entry.focus_set()

        def ok():
            val = cfg.normalize_phone(var.get(), cfg.DEFAULT_PHONE_COUNTRY_CODE)
            if not cfg.is_valid_phone(val):
                messagebox.showwarning(
                    "Телефон",
                    "Введите корректный номер телефона в международном формате "
                    "(например, +375 29 123 45 67).")
                return
            self.phone_var.set(val)
            self._save_phone()
            win.destroy()

        ttk.Button(win, text="OK", command=ok).pack(pady=10)
        entry.bind("<Return>", lambda e: ok())
        self.root.wait_window(win)

    def _save_phone(self):
        cc = self.machine_cfg.get("phone_country_code", cfg.DEFAULT_PHONE_COUNTRY_CODE)
        phone = cfg.normalize_phone(self.phone_var.get(), cc)
        self.phone_var.set(phone)
        self.user_data["phone"] = phone
        self.user_data["anydesk"] = self.anydesk_var.get().strip()
        self.user_data["rustdesk"] = self.rustdesk_var.get().strip()
        self.user_data["comment"] = self.comment.get("1.0", "end").strip()
        cfg.save_user_data(self.user_data)

    # ------------------------------------------------------------ screenshot
    def _take_screenshot(self):
        try:
            self.screenshot_path = shot.capture()
            self.shot_label.configure(text=f"готов: {os.path.basename(self.screenshot_path)}",
                                      foreground="#1a7")
        except Exception as e:
            messagebox.showerror("Скриншот", f"Не удалось сделать скриншот:\n{e}")

    def _clear_screenshot(self):
        self.screenshot_path = None
        self.shot_label.configure(text="скриншот не сделан", foreground="#777")

    # ---------------------------------------------------------------- voice
    def _toggle_voice(self):
        if not self.recording:
            fd, path = tempfile.mkstemp(prefix="csl_voice_", suffix=".wav")
            os.close(fd)
            self.recorder = audio.AudioRecorder()
            self.recorder.start(path)
            self.voice_path = path
            self.recording = True
            self.voice_btn.configure(text="Остановить запись")
            self.voice_label.configure(text="идёт запись...", foreground="#c00")
            self._tick_timer()
        else:
            self.recorder.stop()
            self.recording = False
            self.voice_btn.configure(text="Записать голос")
            self.voice_label.configure(text=f"готов: {os.path.basename(self.voice_path)}",
                                       foreground="#1a7")

    def _tick_timer(self):
        if self.recording:
            self.voice_label.configure(text="идёт запись...", foreground="#c00")
            self.root.after(500, self._tick_timer)

    def _clear_voice(self):
        if self.recording:
            try:
                self.recorder.stop()
            except Exception:
                pass
            self.recording = False
            self.voice_btn.configure(text="Записать голос")
        self.voice_path = None
        self.voice_label.configure(text="голосовое не записано", foreground="#777")

    # ----------------------------------------------------------- auto-update
    def _auto_check_update(self):
        threading.Thread(target=self._check_update_thread, args=(False,), daemon=True).start()

    def _manual_check_update(self):
        self.update_btn.configure(state="disabled", text="Проверка...")
        threading.Thread(target=self._check_update_thread, args=(True,), daemon=True).start()

    def _check_update_thread(self, manual: bool):
        try:
            repo = self.machine_cfg.get("github_repo")
            token = self.machine_cfg.get("github_token")
            if not repo:
                if manual:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Обновления", "Автообновление не настроено администратором."))
                return
            rel = updater.get_latest_release(repo, token)
            if not rel:
                if manual:
                    self.root.after(0, lambda: messagebox.showinfo("Обновления", "Нет доступных релизов."))
                return
            tag = rel.get("tag_name", "")
            if updater.is_newer(tag, APP_VERSION):
                asset = updater.find_exe_asset(rel)
                if not asset:
                    if manual:
                        self.root.after(0, lambda: messagebox.showinfo(
                            "Обновления", "В последнем релизе нет EXE-файла."))
                    return
                self._pending_update = (asset["browser_download_url"], token)
                self.root.after(0, lambda: self._notify_update(tag))
            else:
                if manual:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Обновления", f"Установлена последняя версия (v{APP_VERSION})."))
        except Exception as e:
            if manual:
                msg = f"Не удалось проверить обновление:\n{e}"
                self.root.after(0, lambda: messagebox.showerror("Обновления", msg))
        finally:
            if not self._pending_update:
                self.root.after(0, lambda: self.update_btn.configure(
                    state="normal", text="Проверить обновления"))

    def _notify_update(self, tag):
        self.update_btn.configure(text=f"Обновить до {tag}", command=self._do_update, state="normal")
        self.status.set(f"Доступно обновление {tag}")

    def _do_update(self):
        if not self._pending_update:
            return
        url, token = self._pending_update
        try:
            updater.perform_update(url, token)
            self.root.quit()
            sys.exit(0)
        except Exception as e:
            messagebox.showerror("Обновление", f"Не удалось обновить:\n{e}")

    def _about(self):
        msg = (f"CSL Helpdesk Client\nВерсия приложения: {APP_VERSION}\n")
        if BUILD_DATE:
            msg += f"Дата сборки: {BUILD_DATE}\n"
        if self.machine_cfg.get("github_repo"):
            msg += f"Репозиторий обновлений: {self.machine_cfg.get('github_repo')}\n"
        msg += "\nЗаявки отправляются на h.csl.by (Jira)."
        messagebox.showinfo("О программе", msg)

    # --------------------------------------------------------------- submit
    def _on_submit(self):
        self._save_phone()
        phone = self.phone_var.get().strip()
        if not cfg.is_valid_phone(phone):
            messagebox.showwarning(
                "Телефон",
                "Укажите корректный контактный телефон в международном формате "
                "(например, +375 29 123 45 67) — он обязателен для отправки заявки.")
            return
        selected = [(group, label) for group, label, v in self.fault_vars if v.get()]
        if not selected:
            messagebox.showwarning("Неисправность", "Отметьте хотя бы одну неисправность.")
            return
        if not cfg.is_configured():
            messagebox.showerror("Ошибка настройки",
                                 "Приложение не настроено администратором (нет учётных данных Jira).")
            return

        desc_lines = []
        desc_lines.append(f"Контактный телефон: {phone}")
        desc_lines.append(f"AnyDesk ID: {self.anydesk_var.get().strip()}")
        desc_lines.append(f"RustDesk ID: {self.rustdesk_var.get().strip()}")
        desc_lines.append("")
        desc_lines.append("h3. Отмеченные неисправности")
        by_group = {}
        for group, label in selected:
            by_group.setdefault(group, []).append(label)
        for group, labels in by_group.items():
            desc_lines.append(f"**{group}:**")
            for lab in labels:
                desc_lines.append(f"* {lab}")
            desc_lines.append("")
        desc_lines.append("")
        comment = self.comment.get("1.0", "end").strip()
        if comment:
            desc_lines.append("h3. Комментарий пользователя")
            desc_lines.append(comment)
            desc_lines.append("")
        desc_lines.append(sysinfo.format_system_info_markup(self.system_info))
        description = "\n".join(desc_lines)

        summary = f"Заявка: {selected[0][1]} — {self.system_info.get('hostname', '')}"
        attachments = [p for p in (self.screenshot_path, self.voice_path) if p]

        self.submit_btn.configure(state="disabled")
        self.status.set("Отправка заявки...")
        threading.Thread(target=self._send, args=(summary, description, attachments),
                         daemon=True).start()

    def _send(self, summary, description, attachments):
        try:
            client = jira.JiraClient(
                base_url=self.machine_cfg["jira_url"],
                username=self.machine_cfg["api_user"],
                password=self.machine_cfg["api_password"],
                project_key=self.machine_cfg["project_key"],
                issue_type=self.machine_cfg.get("issue_type", "Запрос"),
                verify_ssl=self.machine_cfg.get("verify_ssl", True),
            )
            result = client.submit(summary, description, attachments)
            key = result["key"]
            url = f"{self.machine_cfg['jira_url']}/browse/{key}"
            self.root.after(0, lambda: self._on_success(key, url, result))
        except jira.JiraError as e:
            msg = str(e)
            self.root.after(0, lambda: self._on_error(msg))
        except Exception as e:
            msg = f"Неожиданная ошибка: {e}"
            self.root.after(0, lambda: self._on_error(msg))

    def _on_success(self, key, url, result):
        self.submit_btn.configure(state="normal")
        self.status.set(f"Заявка {key} создана.")
        messagebox.showinfo("Заявка отправлена",
                            f"Заявка успешно создана: {key}\n\n"
                            f"Вложений: {', '.join(result['attachments']) or 'нет'}\n\n"
                            f"Ссылка: {url}")
        self._clear_screenshot()
        self._clear_voice()

    def _on_error(self, msg):
        self.submit_btn.configure(state="normal")
        self.status.set("Ошибка отправки.")
        messagebox.showerror("Ошибка отправки", msg)


def main():
    root = tk.Tk()
    try:
        root.geometry("680x850")
        root.minsize(620, 760)
    except Exception:
        pass
    HelpdeskApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
