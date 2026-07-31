import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, List, Dict

import customtkinter as ctk
import requests

from models import ConfigManager
from services import WordstatProcessor

COLORS = {
    "bg": "#121212", "fg": "#e0e0e0", "card_bg": "#1e1e1e", "card_border": "#333333",
    "accent": "#e94560", "accent_hover": "#c73650", "success": "#00b894", "warning": "#fdcb6e",
    "log_bg": "#0d1117", "log_fg": "#c9d1d9", "entry_bg": "#2a2a2a", "tab_active": "#e94560",
    "tab_inactive": "#1e1e1e", "progress_bg": "#333333", "progress_fill": "#e94560",
    "table_header": "#0f3460", "table_row_even": "#1e1e1e", "table_row_odd": "#252525",
}


class PasteableEntry(ctk.CTkEntry):
    """
    Кастомный CTkEntry с принудительной поддержкой вставки из буфера обмена.
    Решает проблему, когда Ctrl+V / Cmd+V не работают в стандартном CTkEntry.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Привязываем горячие клавиши для Windows/Linux и macOS
        self.bind("<Control-v>", self._paste)
        self.bind("<Control-V>", self._paste)
        self.bind("<Command-v>", self._paste)
        self.bind("<Command-V>", self._paste)

    def _paste(self, event):
        try:
            text = self.clipboard_get()
            self.insert(tk.INSERT, text)
        except tk.TclError:
            pass
        return "break"

class AccountDialog(ctk.CTkToplevel):
    def __init__(self, parent, config: ConfigManager):
        super().__init__(parent)
        self.title("Управление аккаунтами")
        self.geometry("480x480")
        self.resizable(False, False)
        self.config = config

        self.configure(fg_color=COLORS["bg"])
        self.transient(parent)
        self.grab_set()
        self._build_ui()

    def _build_ui(self):
        ctk.CTkLabel(self, text="Добавить новый аккаунт", font=ctk.CTkFont(size=15, weight="bold")).pack(pady=(15, 5))

        add_frame = ctk.CTkFrame(self, fg_color=COLORS["card_bg"], corner_radius=10)
        add_frame.pack(padx=20, fill="x")

        ctk.CTkLabel(add_frame, text="API Key:").pack(anchor="w", padx=15, pady=(10, 2))
        self.api_key_entry = PasteableEntry(add_frame, width=380, fg_color=COLORS["entry_bg"])
        self.api_key_entry.pack(padx=15, pady=(0, 5))

        ctk.CTkLabel(add_frame, text="Folder ID:").pack(anchor="w", padx=15, pady=(5, 2))
        self.folder_id_entry = PasteableEntry(add_frame, width=380, fg_color=COLORS["entry_bg"])
        self.folder_id_entry.pack(padx=15, pady=(0, 10))

        self.status_label = ctk.CTkLabel(self, text="", text_color=COLORS["warning"], wraplength=400)
        self.status_label.pack(pady=5)

        self.test_btn = ctk.CTkButton(self, text="Проверить и добавить", fg_color=COLORS["success"],
                                      hover_color="#00a381", command=self._test_and_add)
        self.test_btn.pack(pady=5)

        ctk.CTkFrame(self, height=2, fg_color=COLORS["card_border"]).pack(fill="x", padx=20, pady=15)

        ctk.CTkLabel(self, text="Управление существующими", font=ctk.CTkFont(size=15, weight="bold")).pack(pady=(0, 5))

        del_frame = ctk.CTkFrame(self, fg_color=COLORS["card_bg"], corner_radius=10)
        del_frame.pack(padx=20, fill="x")

        self.acc_combo = ctk.CTkComboBox(del_frame, values=self._get_acc_options(), state="readonly", width=380,
                                         fg_color=COLORS["entry_bg"])
        self.acc_combo.pack(padx=15, pady=(15, 10))
        if self.config.accounts:
            self.acc_combo.set(self._get_acc_options()[0])

        self.del_btn = ctk.CTkButton(del_frame, text="Удалить выбранный аккаунт", fg_color=COLORS["accent"],
                                     hover_color=COLORS["accent_hover"], command=self._delete_account)
        self.del_btn.pack(padx=15, pady=(0, 15))

    def _get_acc_options(self) -> List[str]:
        options = []
        for i, acc in enumerate(self.config.accounts):
            masked_key = f"{acc.config.api_key[:6]}...{acc.config.api_key[-4:]}"
            options.append(f"Аккаунт {i + 1}: {masked_key} | Folder: {acc.config.folder_id}")
        return options if options else ["Нет добавленных аккаунтов"]

    def _refresh_accounts_list(self):
        options = self._get_acc_options()
        self.acc_combo.configure(values=options)
        if options:
            self.acc_combo.set(options[0])
        self.del_btn.configure(state="normal" if self.config.accounts else "disabled")

    def _delete_account(self):
        if not self.config.accounts:
            return

        current_val = self.acc_combo.get()
        try:
            idx_str = current_val.split(":")[0].replace("Аккаунт ", "").strip()
            idx = int(idx_str) - 1
        except:
            return

        if messagebox.askyesno("Подтверждение",
                               f"Вы уверены, что хотите удалить Аккаунт {idx + 1}?\nЭто действие необратимо."):
            self.config.remove_account(idx)
            self.status_label.configure(text="Аккаунт успешно удален.", text_color=COLORS["success"])
            self._refresh_accounts_list()

    def _test_and_add(self):
        api_key = self.api_key_entry.get().strip()
        folder_id = self.folder_id_entry.get().strip()

        if not api_key or not folder_id:
            self.status_label.configure(text="Заполните оба поля", text_color=COLORS["accent"])
            return

        self.test_btn.configure(state="disabled", text="Проверка...")
        self.status_label.configure(text="Отправка тестового запроса...", text_color=COLORS["fg"])
        threading.Thread(target=self._api_test, args=(api_key, folder_id), daemon=True).start()

    def _api_test(self, api_key: str, folder_id: str):
        url = "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"
        headers = {"Authorization": f"Api-Key {api_key}", "Content-Type": "application/json"}
        data = {"folderId": folder_id, "phrase": "тест", "numPhrases": "1", "regions": ["225"],
                "devices": ["DEVICE_ALL"]}

        try:
            resp = requests.post(url, headers=headers, json=data, timeout=10)
            if resp.status_code in [200, 429]:
                self.after(0, self._on_success, api_key, folder_id, resp.status_code == 429)
            else:
                self.after(0, self._on_fail, f"Ошибка {resp.status_code}: {resp.text[:50]}")
        except Exception as e:
            self.after(0, self._on_fail, f"Сетевая ошибка: {str(e)}")

    def _on_success(self, api_key, folder_id, is_blocked):
        self.config.add_account(api_key, folder_id)
        msg = "Успешно добавлен!"
        if is_blocked: msg += " (Но сейчас аккаунт заблокирован лимитами)"
        self.status_label.configure(text=msg, text_color=COLORS["success"])
        self.test_btn.configure(state="normal", text="Проверить и добавить")
        self.api_key_entry.delete(0, tk.END)
        self.folder_id_entry.delete(0, tk.END)
        self._refresh_accounts_list()

    def _on_fail(self, error_msg):
        self.status_label.configure(text=f"Ошибка: {error_msg}", text_color=COLORS["accent"])
        self.test_btn.configure(state="normal", text="Проверить и добавить")


class WordstatGUI:
    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title("Yandex Wordstat Parser")
        self.root.geometry("960x750")
        self.root.minsize(850, 650)
        self.root.configure(fg_color=COLORS["bg"])

        self.worker: Optional[WordstatProcessor] = None
        self.log_queue: queue.Queue = queue.Queue()
        self._results: List[Dict] = []

        try:
            self.config = ConfigManager()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить конфигурацию:\n{e}")
            self.config = ConfigManager()

        self.queries_path_var = ctk.StringVar()
        self.progress_var = ctk.DoubleVar(value=0.0)
        self.status_var = ctk.StringVar(value="Готов к работе")

        self._build_header()
        self._build_tabs()
        self._build_parsing_tab()
        self._build_results_tab()
        self._build_settings_tab()
        self._poll_log_queue()

    def _build_header(self):
        header = ctk.CTkFrame(self.root, height=56, corner_radius=0, fg_color=COLORS["card_bg"])
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        ctk.CTkLabel(header, text="  Yandex Wordstat Parser", font=ctk.CTkFont(size=18, weight="bold")).pack(
            side="left", padx=12, pady=10)

        self.accounts_btn = ctk.CTkButton(header, text="Управление аккаунтами", width=180, height=32, corner_radius=8,
                                          fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                                          command=self._open_account_dialog)
        self.accounts_btn.pack(side="right", padx=16, pady=10)

    def _build_tabs(self):
        self.tabview = ctk.CTkTabview(self.root, corner_radius=12,
                                      segmented_button_fg_color=COLORS["tab_inactive"],
                                      segmented_button_selected_color=COLORS["accent"])
        self.tabview.pack(fill="both", expand=True, padx=16, pady=(8, 16))

        self.tab_parsing = self.tabview.add("Парсинг")
        self.tab_results = self.tabview.add("Результаты")
        self.tab_settings = self.tabview.add("Настройки")

    def _build_parsing_tab(self):
        tab = self.tab_parsing

        files_frame = ctk.CTkFrame(tab, corner_radius=12, fg_color=COLORS["card_bg"], border_width=1,
                                   border_color=COLORS["card_border"])
        files_frame.pack(fill="x", padx=8, pady=(8, 6))

        ctk.CTkLabel(files_frame, text="Источник данных", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0,
                                                                                                         column=0,
                                                                                                         columnspan=3,
                                                                                                         sticky="w",
                                                                                                         padx=16,
                                                                                                         pady=(12, 6))

        ctk.CTkLabel(files_frame, text="Файл с запросами (.txt, каждая фраза с новой строки):").grid(row=1, column=0,
                                                                                                     sticky="w",
                                                                                                     padx=16, pady=4)

        PasteableEntry(files_frame, textvariable=self.queries_path_var, width=420, fg_color=COLORS["entry_bg"],
                       corner_radius=8).grid(row=1, column=1, padx=6, pady=4)
        ctk.CTkButton(files_frame, text="Обзор", width=80, corner_radius=8, fg_color=COLORS["accent"],
                      hover_color=COLORS["accent_hover"], command=self._browse_queries).grid(row=1, column=2,
                                                                                             padx=(0, 16), pady=4)

        ctk.CTkLabel(files_frame, text="").grid(row=2, column=0, pady=6)

        controls_frame = ctk.CTkFrame(tab, fg_color="transparent")
        controls_frame.pack(fill="x", padx=8, pady=4)

        self.start_btn = ctk.CTkButton(controls_frame, text="Начать парсинг", width=160, height=40, corner_radius=10,
                                       fg_color=COLORS["success"], hover_color="#00a381",
                                       font=ctk.CTkFont(size=14, weight="bold"), command=self._start_parsing)
        self.start_btn.pack(side="left", padx=4)

        self.stop_btn = ctk.CTkButton(controls_frame, text="Остановить", width=140, height=40, corner_radius=10,
                                      fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                                      font=ctk.CTkFont(size=14, weight="bold"), state="disabled",
                                      command=self._stop_parsing)
        self.stop_btn.pack(side="left", padx=8)

        self.status_label = ctk.CTkLabel(controls_frame, textvariable=self.status_var, font=ctk.CTkFont(size=12),
                                         text_color="gray")
        self.status_label.pack(side="right", padx=8)

        progress_frame = ctk.CTkFrame(tab, fg_color="transparent")
        progress_frame.pack(fill="x", padx=8, pady=6)

        self.progress_bar = ctk.CTkProgressBar(progress_frame, height=14, corner_radius=7,
                                               progress_color=COLORS["progress_fill"], fg_color=COLORS["progress_bg"])
        self.progress_bar.pack(fill="x", padx=4)
        self.progress_bar.set(0)

        self.progress_label = ctk.CTkLabel(progress_frame, text="0 / 0", font=ctk.CTkFont(size=11), text_color="gray")
        self.progress_label.pack(anchor="e", padx=8)

        stats_frame = ctk.CTkFrame(tab, fg_color="transparent")
        stats_frame.pack(fill="x", padx=8, pady=6)
        stats_frame.columnconfigure((0, 1, 2), weight=1)

        self.card_processed = self._create_stat_card(stats_frame, "Обработано", "0 / 0", COLORS["accent"])
        self.card_processed.grid(row=0, column=0, padx=4, pady=4, sticky="ew")

        self.card_found = self._create_stat_card(stats_frame, "Найдено", "0", COLORS["success"])
        self.card_found.grid(row=0, column=1, padx=4, pady=4, sticky="ew")

        self.card_remaining = self._create_stat_card(stats_frame, "Осталось запросов (акк. 1)", "0", "#74b9ff")
        self.card_remaining.grid(row=0, column=2, padx=4, pady=4, sticky="ew")

        ctk.CTkLabel(tab, text="Лог выполнения", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=12,
                                                                                                pady=(8, 2))

        self.log_console = ctk.CTkTextbox(tab, height=180, fg_color=COLORS["log_bg"], text_color=COLORS["log_fg"],
                                          font=ctk.CTkFont(family="Consolas", size=12), corner_radius=10,
                                          border_width=1, border_color=COLORS["card_border"], state="disabled",
                                          wrap="word")
        self.log_console.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _create_stat_card(self, master, title, value, color):
        frame = ctk.CTkFrame(master, corner_radius=12, fg_color=COLORS["card_bg"], border_width=1, border_color=color)
        lbl_title = ctk.CTkLabel(frame, text=title.upper(), font=ctk.CTkFont(size=11, weight="bold"), text_color=color)
        lbl_title.pack(padx=16, pady=(14, 2), anchor="w")
        lbl_value = ctk.CTkLabel(frame, text=value, font=ctk.CTkFont(size=26, weight="bold"))
        lbl_value.pack(padx=16, pady=(0, 14), anchor="w")
        frame._value_label = lbl_value
        return frame

    def _build_results_tab(self):
        tab = self.tab_results

        header_frame = ctk.CTkFrame(tab, fg_color="transparent")
        header_frame.pack(fill="x", padx=8, pady=8)

        ctk.CTkLabel(header_frame, text="Таблица результатов", font=ctk.CTkFont(size=16, weight="bold")).pack(
            side="left")

        self.save_report_btn = ctk.CTkButton(header_frame, text="Сохранить отчет как...", width=180, height=34,
                                             corner_radius=8,
                                             fg_color=COLORS["success"], hover_color="#00a381", state="disabled",
                                             command=self._save_report_dialog)
        self.save_report_btn.pack(side="right", padx=10)

        self.results_count_label = ctk.CTkLabel(header_frame, text="Записей: 0", font=ctk.CTkFont(size=12),
                                                text_color="gray")
        self.results_count_label.pack(side="right", padx=10)

        table_frame = ctk.CTkFrame(tab, corner_radius=10, fg_color=COLORS["card_bg"], border_width=1,
                                   border_color=COLORS["card_border"])
        table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Custom.Treeview", background=COLORS["card_bg"], foreground=COLORS["fg"],
                        fieldbackground=COLORS["card_bg"], font=("Segoe UI", 11), rowheight=34, borderwidth=0)
        style.configure("Custom.Treeview.Heading", background=COLORS["table_header"], foreground="white",
                        font=("Segoe UI", 11, "bold"), borderwidth=0, relief="flat")
        style.map("Custom.Treeview", background=[("selected", COLORS["accent"])])

        columns = ("query", "normal", "quoted")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", style="Custom.Treeview")

        self.tree.heading("query", text="Запрос", anchor="w")
        self.tree.heading("normal", text="Показов (обычный)", anchor="center")
        self.tree.heading("quoted", text="Показов (в кавычках)", anchor="center")

        self.tree.column("query", width=450, anchor="w")
        self.tree.column("normal", width=180, anchor="center")
        self.tree.column("quoted", width=180, anchor="center")

        self.tree.tag_configure('oddrow', background=COLORS["table_row_odd"])
        self.tree.tag_configure('evenrow', background=COLORS["table_row_even"])

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        scrollbar.pack(side="right", fill="y", pady=4)

    def _build_settings_tab(self):
        tab = self.tab_settings

        settings_frame = ctk.CTkFrame(tab, corner_radius=12, fg_color=COLORS["card_bg"], border_width=1,
                                      border_color=COLORS["card_border"])
        settings_frame.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(settings_frame, text="Глобальные настройки парсинга",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=20, pady=(20, 10))

        grid = ctk.CTkFrame(settings_frame, fg_color="transparent")
        grid.pack(padx=20, fill="x")
        grid.columnconfigure(1, weight=1)

        self._add_setting_row(grid, 0, "Лимит запросов в час (на аккаунт):", "max_requests",
                              self.config.settings.max_requests_per_hour)
        self._add_setting_row(grid, 1, "Задержка между запросами (сек):", "request_delay",
                              self.config.settings.request_delay)

        ctk.CTkLabel(settings_frame, text="Пороги фильтрации", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=20, pady=(20, 5))

        self._add_setting_row(grid, 2, "Мин. показов (обычный запрос):", "min_normal",
                              self.config.settings.min_normal_count)
        self._add_setting_row(grid, 3, "Мин. показов (в кавычках):", "min_quoted",
                              self.config.settings.min_quoted_count)

        save_btn = ctk.CTkButton(settings_frame, text="Сохранить настройки", fg_color=COLORS["success"],
                                 hover_color="#00a381", command=self._save_settings)
        save_btn.pack(anchor="w", padx=20, pady=20)

    def _add_setting_row(self, parent, row, label_text, key, default_val):
        ctk.CTkLabel(parent, text=label_text).grid(row=row, column=0, sticky="w", pady=8, padx=(0, 10))
        entry = PasteableEntry(parent, width=150, fg_color=COLORS["entry_bg"])
        entry.insert(0, str(default_val))
        entry.grid(row=row, column=1, sticky="w", pady=8)
        setattr(self, f"entry_{key}", entry)

    def _save_settings(self):
        try:
            self.config.settings.max_requests_per_hour = int(self.entry_max_requests.get())
            self.config.settings.request_delay = float(self.entry_request_delay.get())
            self.config.settings.min_normal_count = int(self.entry_min_normal.get())
            self.config.settings.min_quoted_count = int(self.entry_min_quoted.get())
            self.config.save()
            messagebox.showinfo("Успех", "Настройки успешно сохранены.")
        except ValueError:
            messagebox.showerror("Ошибка", "Пожалуйста, введите корректные числовые значения.")

    def _browse_queries(self):
        path = ctk.filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path: self.queries_path_var.set(path)

    def _open_account_dialog(self):
        AccountDialog(self.root, self.config)

    def _start_parsing(self):
        queries_file = self.queries_path_var.get()
        if not queries_file or not os.path.exists(queries_file):
            messagebox.showerror("Ошибка", "Укажите корректный файл с запросами.")
            return

        if not self.config.accounts:
            messagebox.showerror("Ошибка", "Нет добавленных аккаунтов.")
            return

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.save_report_btn.configure(state="disabled")
        self.progress_bar.set(0)
        self.progress_label.configure(text="0 / 0")
        self._results = []
        self._log("Запуск парсинга...")

        self.worker = WordstatProcessor(
            config=self.config,
            queries_file=queries_file,
            log_callback=lambda msg: self.log_queue.put(msg),
            progress_callback=self._update_progress,
            stats_callback=self._update_stats,
            finish_callback=self._on_finish
        )
        threading.Thread(target=self.worker.run, daemon=True).start()

    def _stop_parsing(self):
        if self.worker:
            self.worker.stop()
            self._log("Отправка сигнала остановки...")

    def _update_progress(self, current: int, total: int):
        def _apply():
            percent = (current / total) * 100 if total > 0 else 0
            self.progress_bar.set(percent / 100)
            self.progress_label.configure(text=f"{current} / {total}")
            self.status_var.set(f"Обработка: {current}/{total}")
            self.card_processed._value_label.configure(text=f"{current} / {total}")

        self.root.after(0, _apply)

    def _update_stats(self, remaining: int, acc_idx: int, acc_total: int):
        def _apply():
            self.card_remaining._value_label.configure(text=str(remaining))
            parent = self.card_remaining.master
            self.card_remaining.destroy()
            self.card_remaining = self._create_stat_card(parent, f"Осталось запросов (акк. {acc_idx})", str(remaining),
                                                         "#74b9ff")
            self.card_remaining.grid(row=0, column=2, padx=4, pady=4, sticky="ew")

        self.root.after(0, _apply)

    def _on_finish(self, results: List[Dict]):
        def _apply():
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self._results = results
            self._refresh_results_table()

            if results:
                self._log(f"Парсинг завершен. Найдено {len(results)} подходящих запросов.")
                self.save_report_btn.configure(state="normal")
                self.tabview.set("Результаты")
            else:
                self._log("Парсинг завершен. Подходящих запросов не найдено.")

        self.root.after(0, _apply)

    def _save_report_dialog(self):
        path = ctk.filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
            initialfile="wordstat_results.xlsx",
            title="Сохранить отчет"
        )

        if path:
            try:
                WordstatProcessor.export_results(self._results, path)
                messagebox.showinfo("Успех", f"Отчет успешно сохранен:\n{path}")

                if os.path.exists("progress.json"):
                    os.remove("progress.json")

            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить файл:\n{e}")

    def _refresh_results_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for i, row in enumerate(self._results):
            tag = 'oddrow' if i % 2 else 'evenrow'
            self.tree.insert("", "end", values=(
                row.get("Запрос", ""),
                row.get("Показов (обычный)", 0),
                row.get("Показов (в кавычках)", 0),
            ), tags=(tag,))

        self.results_count_label.configure(text=f"Записей: {len(self._results)}")
        self.card_found._value_label.configure(text=str(len(self._results)))

    def _log(self, message: str):
        self.log_queue.put(message)

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_console.configure(state="normal")
                self.log_console.insert("end", msg + "\n")
                self.log_console.see("end")
                self.log_console.configure(state="disabled")
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_log_queue)

    def run(self):
        self.root.mainloop()