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
        try:
            self.root.iconbitmap(default=os.path.join(os.path.dirname(__file__), "app.ico"))
        except Exception:
            pass

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
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        # Вся форма прокручивается, в том числе на небольшом экране и при
        # увеличенном масштабе Windows. Горизонтальная полоса нужна для длинных подписей.
        viewport = ttk.Frame(self.root)
        viewport.pack(fill="both", expand=True)
        canvas = tk.Canvas(viewport, highlightthickness=0)
        vbar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
        hbar = ttk.Scrollbar(viewport, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        vbar.pack(side="right", fill="y")
        hbar.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True)
        f = ttk.Frame(canvas, padding=8)
        window = canvas.create_window((0, 0), window=f, anchor="nw")

        def update_scrollregion(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def resize_content(event):
            canvas.itemconfigure(window, width=max(event.width, f.winfo_reqwidth()))
            update_scrollregion()

        f.bind("<Configure>", update_scrollregion)
        canvas.bind("<Configure>", resize_content)

        def scroll_wheel(event):
            # Text виджеты обрабатывают колесо самостоятельно.
            widget = event.widget
            if isinstance(widget, tk.Text):
                return
            if str(widget).startswith(str(canvas)):
                canvas.yview_scroll(-int(event.delta / 120), "units")
                return "break"

        self.root.bind_all("<MouseWheel>", scroll_wheel, add="+")

        ttk.Label(f, text="Заявка в техподдержку CSL",
                  font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(f, text="Опишите проблему — заявка уйдёт администратору автоматически.",
                  foreground="#555").pack(anchor="w")

        # --- Контактные данные ---
        contact = ttk.LabelFrame(f, text="Контактные данные")
        contact.pack(fill="x", pady=6)
        ttk.Label(contact, text="Контактный телефон:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.phone_var = tk.StringVar(value=self.user_data.get("phone", ""))
        self.phone_entry = ttk.Entry(contact, textvariable=self.phone_var, width=30)
        self.phone_entry.grid(row=0, column=1, sticky="w", padx=6, pady=4)
        self.phone_entry.bind("<FocusOut>", lambda e: self._save_phone())
        ttk.Button(contact, text="Сохранить", command=lambda: self._save_phone(show_error=True)).grid(row=0, column=2, padx=6)

        ttk.Label(contact, text="AnyDesk ID:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.anydesk_var = tk.StringVar(value=self.user_data.get("anydesk") or sysinfo.get_anydesk_id())
        ttk.Entry(contact, textvariable=self.anydesk_var, width=30).grid(row=1, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(contact, text="RustDesk ID:").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.rustdesk_var = tk.StringVar(value=self.user_data.get("rustdesk") or sysinfo.get_rustdesk_id())
        ttk.Entry(contact, textvariable=self.rustdesk_var, width=30).grid(row=2, column=1, sticky="w", padx=6, pady=4)

        # --- Неисправности (чекбоксы) ---
        faults = ttk.LabelFrame(f, text="Что не работает? (отметьте нужное)")
        faults.pack(fill="x", pady=6)
        self.fault_vars = []
        for i, label in enumerate(self.machine_cfg.get("faults", cfg.DEFAULT_FAULTS)):
            v = tk.BooleanVar()
            self.fault_vars.append((label, v))
            cb = ttk.Checkbutton(faults, text=label, variable=v)
            cb.grid(row=i // 2, column=i % 2, sticky="w", padx=8, pady=2)

        # --- Комментарий ---
        cm = ttk.LabelFrame(f, text="Комментарий (текст)")
        cm.pack(fill="x", pady=6)
        self.comment = tk.Text(cm, height=4, width=60, wrap="word")
        self.comment.pack(fill="x", padx=6, pady=4)
        if self.user_data.get("comment"):
            self.comment.insert("1.0", self.user_data.get("comment"))

        # --- Вложения: скриншот и голос ---
        att = ttk.LabelFrame(f, text="Вложения")
        att.pack(fill="x", pady=6)

        self.shot_btn = ttk.Button(att, text="Сделать скриншот", command=self._take_screenshot)
        self.shot_btn.grid(row=0, column=0, padx=6, pady=4)
        self.shot_label = ttk.Label(att, text="скриншот не сделан", foreground="#777")
        self.shot_label.grid(row=0, column=1, padx=6, pady=4)
        self.shot_clear = ttk.Button(att, text="Убрать", command=self._clear_screenshot)
        self.shot_clear.grid(row=0, column=2, padx=6, pady=4)

        self.voice_btn = ttk.Button(att, text="Записать голос", command=self._toggle_voice)
        self.voice_btn.grid(row=1, column=0, padx=6, pady=4)
        self.voice_label = ttk.Label(att, text="голосовое не записано", foreground="#777")
        self.voice_label.grid(row=1, column=1, padx=6, pady=4)
        self.voice_clear = ttk.Button(att, text="Убрать", command=self._clear_voice)
        self.voice_clear.grid(row=1, column=2, padx=6, pady=4)

        if not audio.is_available():
            self.voice_label.configure(text="проверьте микрофон и разрешения")

        # --- Кнопки действий ---
        actions = ttk.Frame(f)
        actions.pack(pady=8)
        self.submit_btn = ttk.Button(actions, text="Отправить заявку", command=self._on_submit)
        self.submit_btn.grid(row=0, column=0, padx=6)
        self.update_btn = ttk.Button(actions, text="Проверить обновления",
                                     command=self._manual_check_update)
        self.update_btn.grid(row=0, column=1, padx=6)
        ttk.Button(actions, text="О программе", command=self._about).grid(row=0, column=2, padx=6)

        # --- Статус ---
        self.status = tk.StringVar(value=f"Готово. Версия {APP_VERSION}")
        ttk.Label(f, textvariable=self.status, foreground="#333").pack(anchor="w")

        # --- Сведения о ПК ---
        sys_frame = ttk.Frame(f)
        sys_frame.pack(fill="x", pady=6)
        sys_scroll = ttk.Scrollbar(sys_frame, orient="vertical")
        self.sys_text = tk.Text(sys_frame, height=10, width=60, wrap="word", state="disabled",
                                bg="#f7f7f7", fg="#222", yscrollcommand=sys_scroll.set)
        sys_scroll.configure(command=self.sys_text.yview)
        sys_scroll.pack(side="right", fill="y")
        self.sys_text.pack(side="left", fill="both", expand=True)

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
            val = var.get().strip()
            if not val:
                messagebox.showwarning("Телефон", "Введите номер телефона.")
                return
            self.phone_var.set(val)
            if self._save_phone(show_error=True):
                win.destroy()

        ttk.Button(win, text="OK", command=ok).pack(pady=10)
        entry.bind("<Return>", lambda e: ok())
        self.root.wait_window(win)

    def _save_phone(self, show_error=False):
        try:
            phone = cfg.normalize_phone(self.phone_var.get())
        except ValueError:
            if show_error:
                messagebox.showwarning("Телефон", "Введите корректный номер. Для номера другой страны укажите код с +.")
            return False
        self.phone_var.set(phone)
        self.user_data["phone"] = phone
        self.user_data["anydesk"] = self.anydesk_var.get().strip()
        self.user_data["rustdesk"] = self.rustdesk_var.get().strip()
        self.user_data["comment"] = self.comment.get("1.0", "end").strip()
        cfg.save_user_data(self.user_data)
        return True

    # ------------------------------------------------------------ screenshot
    def _take_screenshot(self):
        def completed(path):
            if path:
                self.screenshot_path = path
                self.shot_label.configure(text=f"готов: {os.path.basename(path)}", foreground="#1a7")

        shot.select_area(self.root, completed,
                         lambda e: messagebox.showerror("Скриншот", f"Не удалось сделать скриншот:\n{e}"))

    def _clear_screenshot(self):
        self.screenshot_path = None
        self.shot_label.configure(text="скриншот не сделан", foreground="#777")

    # ---------------------------------------------------------------- voice
    def _toggle_voice(self):
        if not self.recording:
            fd, path = tempfile.mkstemp(prefix="csl_voice_", suffix=".wav")
            os.close(fd)
            try:
                self.recorder = audio.AudioRecorder()
                self.recorder.start(path)
            except Exception as e:
                os.unlink(path)
                messagebox.showerror("Запись голоса", f"Не удалось начать запись: {e}\nПроверьте подключение микрофона и разрешение Windows на доступ к нему.")
                return
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
        if not self.machine_cfg.get("update_enabled"):
            messagebox.showinfo("Обновления", "Обновления отключены администратором или не указан репозиторий GitHub в настройках.")
            return
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
                self.root.after(0, lambda error=str(e): messagebox.showerror(
                    "Обновления", f"Не удалось проверить обновление:\n{error}"))
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
        if not self._save_phone(show_error=True):
            return
        selected = [label for label, v in self.fault_vars if v.get()]
        if not selected:
            messagebox.showwarning("Неисправность", "Отметьте хотя бы одну неисправность.")
            return
        if not cfg.is_configured():
            messagebox.showerror("Ошибка настройки",
                                 "Приложение не настроено администратором (нет учётных данных Jira).")
            return

        desc_lines = []
        desc_lines.append(f"Контактный телефон: {self.phone_var.get().strip()}")
        desc_lines.append(f"AnyDesk ID: {self.anydesk_var.get().strip()}")
        desc_lines.append(f"RustDesk ID: {self.rustdesk_var.get().strip()}")
        desc_lines.append("")
        desc_lines.append("h3. Отмеченные неисправности")
        for s in selected:
            desc_lines.append(f"* {s}")
        desc_lines.append("")
        comment = self.comment.get("1.0", "end").strip()
        if comment:
            desc_lines.append("h3. Комментарий пользователя")
            desc_lines.append(comment)
            desc_lines.append("")
        desc_lines.append(sysinfo.format_system_info_markup(self.system_info))
        description = "\n".join(desc_lines)

        summary = f"Заявка: {selected[0]} — {self.system_info.get('hostname', '')}"
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
            self.root.after(0, lambda: self._on_error(str(e)))
        except Exception as e:
            self.root.after(0, lambda: self._on_error(f"Неожиданная ошибка: {e}"))

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
        root.geometry(f"{min(640, root.winfo_screenwidth())}x{min(780, root.winfo_screenheight() - 80)}")
    except Exception:
        pass
    HelpdeskApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
