from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from .client import WordstatClient
from .config import ConfigManager
from .exporter import export_to_excel
from .models import ParseResult
from .processor import WordstatProcessor

COLORS = {
    "bg": "#121212",
    "fg": "#e0e0e0",
    "card_bg": "#1e1e1e",
    "card_border": "#333333",
    "accent": "#e94560",
    "accent_hover": "#c73650",
    "success": "#00b894",
    "success_hover": "#00a381",
    "warning": "#fdcb6e",
    "log_bg": "#0d1117",
    "log_fg": "#c9d1d9",
    "entry_bg": "#2a2a2a",
    "tab_active": "#e94560",
    "tab_inactive": "#1e1e1e",
    "progress_bg": "#333333",
    "progress_fill": "#e94560",
    "table_header": "#0f3460",
    "table_row_even": "#1e1e1e",
    "table_row_odd": "#252525",
    "info": "#74b9ff",
    "danger": "#ff7675",
}


class PasteableEntry(ctk.CTkEntry):
    """CTkEntry с поддержкой Ctrl/Cmd+V."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.bind(
            "<Control-v>",
            self._paste,
        )
        self.bind(
            "<Control-V>",
            self._paste,
        )
        self.bind(
            "<Command-v>",
            self._paste,
        )
        self.bind(
            "<Command-V>",
            self._paste,
        )

    def _paste(self, event=None):
        try:
            self.insert(
                tk.INSERT,
                self.clipboard_get(),
            )
        except tk.TclError:
            pass

        return "break"


class AccountDialog(ctk.CTkToplevel):
    """Окно управления аккаунтами."""

    def __init__(
        self,
        parent: ctk.CTk,
        config: ConfigManager,
    ) -> None:
        super().__init__(parent)

        self.parent = parent
        self.config_manager = config

        self.title("Управление аккаунтами")
        self.geometry("520x540")
        self.resizable(False, False)
        self.configure(
            fg_color=COLORS["bg"],
        )

        self.transient(parent)
        self.grab_set()

        self.protocol(
            "WM_DELETE_WINDOW",
            self._close,
        )

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        ctk.CTkLabel(
            self,
            text="Управление аккаунтами",
            font=ctk.CTkFont(
                size=20,
                weight="bold",
            ),
        ).pack(
            pady=(20, 5),
        )

        ctk.CTkLabel(
            self,
            text=("API Key и Folder ID используются для работы с Yandex Wordstat API."),
            text_color="gray",
            wraplength=440,
        ).pack(
            pady=(0, 15),
        )

        self._build_add_section()
        self._build_accounts_section()

    def _build_add_section(self) -> None:
        frame = ctk.CTkFrame(
            self,
            fg_color=COLORS["card_bg"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["card_border"],
        )
        frame.pack(
            padx=20,
            fill="x",
        )

        ctk.CTkLabel(
            frame,
            text="Добавить аккаунт",
            font=ctk.CTkFont(
                size=15,
                weight="bold",
            ),
        ).pack(
            anchor="w",
            padx=16,
            pady=(15, 10),
        )

        ctk.CTkLabel(
            frame,
            text="API Key",
        ).pack(
            anchor="w",
            padx=16,
            pady=(0, 3),
        )

        self.api_key_entry = PasteableEntry(
            frame,
            width=440,
            fg_color=COLORS["entry_bg"],
        )
        self.api_key_entry.pack(
            padx=16,
            pady=(0, 10),
        )

        ctk.CTkLabel(
            frame,
            text="Folder ID",
        ).pack(
            anchor="w",
            padx=16,
            pady=(0, 3),
        )

        self.folder_id_entry = PasteableEntry(
            frame,
            width=440,
            fg_color=COLORS["entry_bg"],
        )
        self.folder_id_entry.pack(
            padx=16,
            pady=(0, 10),
        )

        self.status_label = ctk.CTkLabel(
            frame,
            text="",
            wraplength=420,
        )
        self.status_label.pack(
            padx=16,
            pady=(0, 8),
        )

        self.test_button = ctk.CTkButton(
            frame,
            text="Проверить и добавить",
            height=36,
            fg_color=COLORS["success"],
            hover_color=COLORS["success_hover"],
            command=self._test_and_add,
        )
        self.test_button.pack(
            padx=16,
            pady=(0, 16),
            fill="x",
        )

    def _build_accounts_section(self) -> None:
        ctk.CTkLabel(
            self,
            text="Сохранённые аккаунты",
            font=ctk.CTkFont(
                size=15,
                weight="bold",
            ),
        ).pack(
            anchor="w",
            padx=20,
            pady=(20, 8),
        )

        frame = ctk.CTkFrame(
            self,
            fg_color=COLORS["card_bg"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["card_border"],
        )
        frame.pack(
            padx=20,
            fill="x",
        )

        self.account_combo = ctk.CTkComboBox(
            frame,
            values=self._account_options(),
            state="readonly",
            width=440,
            fg_color=COLORS["entry_bg"],
        )
        self.account_combo.pack(
            padx=16,
            pady=16,
        )

        self.delete_button = ctk.CTkButton(
            frame,
            text="Удалить выбранный аккаунт",
            height=36,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._delete_account,
        )
        self.delete_button.pack(
            padx=16,
            pady=(0, 16),
            fill="x",
        )

        self._refresh_accounts()

    # ------------------------------------------------------------------
    # Account management
    # ------------------------------------------------------------------

    def _account_options(self) -> list[str]:
        if not self.config_manager.accounts:
            return ["Нет добавленных аккаунтов"]

        options: list[str] = []

        for index, account in enumerate(
            self.config_manager.accounts,
            start=1,
        ):
            api_key = account.config.api_key

            if len(api_key) > 10:
                masked_key = f"{api_key[:6]}...{api_key[-4:]}"
            else:
                masked_key = "*" * len(api_key)

            options.append(
                f"Аккаунт {index}: {masked_key} | Folder: {account.config.folder_id}"
            )

        return options

    def _refresh_accounts(self) -> None:
        options = self._account_options()

        self.account_combo.configure(
            values=options,
        )
        self.account_combo.set(options[0])

        self.delete_button.configure(
            state=("normal" if self.config_manager.accounts else "disabled"),
        )

    def _delete_account(self) -> None:
        if not self.config_manager.accounts:
            return

        selected = self.account_combo.get()

        try:
            account_number = int(selected.split(":")[0].replace("Аккаунт ", "").strip())
        except (ValueError, IndexError):
            return

        index = account_number - 1

        if not 0 <= index < len(self.config_manager.accounts):
            return

        confirmed = messagebox.askyesno(
            "Подтверждение",
            (f"Удалить Аккаунт {account_number}?\n\nЭто действие нельзя отменить."),
            parent=self,
        )

        if not confirmed:
            return

        try:
            removed = self.config_manager.remove_account(index)
        except OSError as error:
            messagebox.showerror(
                "Ошибка",
                f"Не удалось сохранить конфигурацию:\n{error}",
                parent=self,
            )
            return

        if removed:
            self.status_label.configure(
                text="Аккаунт успешно удалён.",
                text_color=COLORS["success"],
            )
            self._refresh_accounts()

    # ------------------------------------------------------------------
    # Account validation
    # ------------------------------------------------------------------

    def _test_and_add(self) -> None:
        api_key = self.api_key_entry.get().strip()
        folder_id = self.folder_id_entry.get().strip()

        if not api_key or not folder_id:
            self.status_label.configure(
                text="Заполните API Key и Folder ID.",
                text_color=COLORS["danger"],
            )
            return

        self.test_button.configure(
            state="disabled",
            text="Проверка...",
        )

        self.status_label.configure(
            text="Проверяем доступ к Wordstat API...",
            text_color=COLORS["fg"],
        )

        threading.Thread(
            target=self._validate_account,
            args=(api_key, folder_id),
            daemon=True,
            name="account-validator",
        ).start()

    def _validate_account(
        self,
        api_key: str,
        folder_id: str,
    ) -> None:
        """
        Проверяет аккаунт через отдельный WordstatClient.

        UI при этом не знает ничего о HTTP/API.
        """

        client = WordstatClient(
            config=self.config_manager,
            log_callback=lambda _: None,
            stats_callback=lambda *_: None,
        )

        try:
            success, status = client.validate_account(
                api_key,
                folder_id,
            )
        finally:
            client.close()

        self.after(
            0,
            self._on_account_validation_finished,
            api_key,
            folder_id,
            success,
            status,
        )

    def _on_account_validation_finished(
        self,
        api_key: str,
        folder_id: str,
        success: bool,
        status: str,
    ) -> None:
        if not success:
            self.status_label.configure(
                text=status,
                text_color=COLORS["danger"],
            )

            self.test_button.configure(
                state="normal",
                text="Проверить и добавить",
            )

            return

        try:
            self.config_manager.add_account(
                api_key,
                folder_id,
            )

        except OSError as error:
            self.status_label.configure(
                text=(f"Не удалось сохранить аккаунт: {error}"),
                text_color=COLORS["danger"],
            )

            self.test_button.configure(
                state="normal",
                text="Проверить и добавить",
            )

            return

        if status == "rate_limited":
            message = "Аккаунт добавлен. API сейчас вернул HTTP 429."
        else:
            message = "Аккаунт успешно проверен и добавлен."

        self.status_label.configure(
            text=message,
            text_color=COLORS["success"],
        )

        self.test_button.configure(
            state="normal",
            text="Проверить и добавить",
        )

        self.api_key_entry.delete(
            0,
            tk.END,
        )
        self.folder_id_entry.delete(
            0,
            tk.END,
        )

        self._refresh_accounts()

    def _close(self) -> None:
        self.grab_release()
        self.destroy()


class WordstatGUI:
    """Главное окно приложения."""

    def __init__(
        self,
        config_path: str | Path = "config.json",
    ) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()

        self.root.title("Yandex Wordstat Parser")

        self.root.geometry("960x750")

        self.root.minsize(
            850,
            650,
        )

        self.root.configure(
            fg_color=COLORS["bg"],
        )

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self._on_close,
        )

        self.worker: WordstatProcessor | None = None

        self.log_queue: queue.Queue[str] = queue.Queue()

        self.results: list[ParseResult] = []

        self.queries_path_var = ctk.StringVar()

        self.status_var = ctk.StringVar(
            value="Готов к работе",
        )

        try:
            self.config = ConfigManager(config_path)
        except (
            OSError,
            ValueError,
        ) as error:
            messagebox.showerror(
                "Ошибка конфигурации",
                (f"Не удалось загрузить конфигурацию:\n\n{error}"),
                parent=self.root,
            )

            self.config = ConfigManager()

        self._build_header()
        self._build_tabs()

        self._build_parsing_tab()
        self._build_results_tab()
        self._build_settings_tab()

        self._poll_log_queue()

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _build_header(self) -> None:
        header = ctk.CTkFrame(
            self.root,
            height=56,
            corner_radius=0,
            fg_color=COLORS["card_bg"],
        )

        header.pack(
            fill="x",
            side="top",
        )

        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text="Yandex Wordstat Parser",
            font=ctk.CTkFont(
                size=18,
                weight="bold",
            ),
        ).pack(
            side="left",
            padx=18,
        )

        ctk.CTkButton(
            header,
            text="Управление аккаунтами",
            width=190,
            height=32,
            corner_radius=8,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._open_account_dialog,
        ).pack(
            side="right",
            padx=16,
        )

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _build_tabs(self) -> None:
        self.tabview = ctk.CTkTabview(
            self.root,
            corner_radius=12,
            segmented_button_fg_color=(COLORS["tab_inactive"]),
            segmented_button_selected_color=(COLORS["accent"]),
        )

        self.tabview.pack(
            fill="both",
            expand=True,
            padx=16,
            pady=(8, 16),
        )

        self.tab_parsing = self.tabview.add("Парсинг")

        self.tab_results = self.tabview.add("Результаты")

        self.tab_settings = self.tabview.add("Настройки")

    # ------------------------------------------------------------------
    # Parsing tab
    # ------------------------------------------------------------------

    def _build_parsing_tab(self) -> None:
        self._build_file_section(self.tab_parsing)

        self._build_control_section(self.tab_parsing)

        self._build_progress_section(self.tab_parsing)

        self._build_stats_section(self.tab_parsing)

        self._build_log_section(self.tab_parsing)

    def _build_file_section(
        self,
        tab,
    ) -> None:
        frame = ctk.CTkFrame(
            tab,
            corner_radius=12,
            fg_color=COLORS["card_bg"],
            border_width=1,
            border_color=COLORS["card_border"],
        )

        frame.pack(
            fill="x",
            padx=8,
            pady=(8, 6),
        )

        ctk.CTkLabel(
            frame,
            text="Источник данных",
            font=ctk.CTkFont(
                size=14,
                weight="bold",
            ),
        ).grid(
            row=0,
            column=0,
            columnspan=3,
            sticky="w",
            padx=16,
            pady=(14, 8),
        )

        ctk.CTkLabel(
            frame,
            text=("Файл с запросами (.txt, одна фраза на строку):"),
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=16,
            pady=(0, 12),
        )

        PasteableEntry(
            frame,
            textvariable=self.queries_path_var,
            width=420,
            fg_color=COLORS["entry_bg"],
            corner_radius=8,
        ).grid(
            row=1,
            column=1,
            padx=6,
            pady=(0, 12),
        )

        ctk.CTkButton(
            frame,
            text="Обзор",
            width=80,
            corner_radius=8,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._browse_queries,
        ).grid(
            row=1,
            column=2,
            padx=(0, 16),
            pady=(0, 12),
        )

    def _build_control_section(
        self,
        tab,
    ) -> None:
        frame = ctk.CTkFrame(
            tab,
            fg_color="transparent",
        )

        frame.pack(
            fill="x",
            padx=8,
            pady=4,
        )

        self.start_button = ctk.CTkButton(
            frame,
            text="Начать парсинг",
            width=160,
            height=40,
            corner_radius=10,
            fg_color=COLORS["success"],
            hover_color=COLORS["success_hover"],
            font=ctk.CTkFont(
                size=14,
                weight="bold",
            ),
            command=self._start_parsing,
        )

        self.start_button.pack(
            side="left",
            padx=4,
        )

        self.stop_button = ctk.CTkButton(
            frame,
            text="Остановить",
            width=140,
            height=40,
            corner_radius=10,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(
                size=14,
                weight="bold",
            ),
            state="disabled",
            command=self._stop_parsing,
        )

        self.stop_button.pack(
            side="left",
            padx=8,
        )

        ctk.CTkLabel(
            frame,
            textvariable=self.status_var,
            text_color="gray",
        ).pack(
            side="right",
            padx=8,
        )

    def _build_progress_section(
        self,
        tab,
    ) -> None:
        frame = ctk.CTkFrame(
            tab,
            fg_color="transparent",
        )

        frame.pack(
            fill="x",
            padx=8,
            pady=6,
        )

        self.progress_bar = ctk.CTkProgressBar(
            frame,
            height=14,
            corner_radius=7,
            progress_color=(COLORS["progress_fill"]),
            fg_color=(COLORS["progress_bg"]),
        )

        self.progress_bar.pack(
            fill="x",
            padx=4,
        )

        self.progress_bar.set(0)

        self.progress_label = ctk.CTkLabel(
            frame,
            text="0 / 0",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )

        self.progress_label.pack(
            anchor="e",
            padx=8,
        )

    def _build_stats_section(
        self,
        tab,
    ) -> None:
        frame = ctk.CTkFrame(
            tab,
            fg_color="transparent",
        )

        frame.pack(
            fill="x",
            padx=8,
            pady=6,
        )

        frame.columnconfigure(
            0,
            weight=1,
        )
        frame.columnconfigure(
            1,
            weight=1,
        )
        frame.columnconfigure(
            2,
            weight=1,
        )

        self.card_processed = self._create_stat_card(
            frame,
            "Обработано",
            "0 / 0",
            COLORS["accent"],
        )

        self.card_processed.grid(
            row=0,
            column=0,
            padx=4,
            pady=4,
            sticky="ew",
        )

        self.card_found = self._create_stat_card(
            frame,
            "Найдено",
            "0",
            COLORS["success"],
        )

        self.card_found.grid(
            row=0,
            column=1,
            padx=4,
            pady=4,
            sticky="ew",
        )

        self.card_remaining = self._create_stat_card(
            frame,
            "Осталось запросов",
            "0",
            COLORS["info"],
        )

        self.card_remaining.grid(
            row=0,
            column=2,
            padx=4,
            pady=4,
            sticky="ew",
        )

    def _create_stat_card(
        self,
        parent,
        title: str,
        value: str,
        color: str,
    ):
        frame = ctk.CTkFrame(
            parent,
            corner_radius=12,
            fg_color=COLORS["card_bg"],
            border_width=1,
            border_color=color,
        )

        title_label = ctk.CTkLabel(
            frame,
            text=title.upper(),
            font=ctk.CTkFont(
                size=11,
                weight="bold",
            ),
            text_color=color,
        )

        title_label.pack(
            padx=16,
            pady=(14, 2),
            anchor="w",
        )

        value_label = ctk.CTkLabel(
            frame,
            text=value,
            font=ctk.CTkFont(
                size=26,
                weight="bold",
            ),
        )

        value_label.pack(
            padx=16,
            pady=(0, 14),
            anchor="w",
        )

        # Сохраняем ссылки на оба элемента,
        # чтобы UI мог обновлять их без поиска
        # среди дочерних widgets.
        frame.title_label = title_label
        frame.value_label = value_label

        return frame

    def _build_log_section(
        self,
        tab,
    ) -> None:
        ctk.CTkLabel(
            tab,
            text="Лог выполнения",
            font=ctk.CTkFont(
                size=13,
                weight="bold",
            ),
        ).pack(
            anchor="w",
            padx=12,
            pady=(8, 2),
        )

        self.log_console = ctk.CTkTextbox(
            tab,
            height=180,
            fg_color=COLORS["log_bg"],
            text_color=COLORS["log_fg"],
            font=ctk.CTkFont(
                family="Consolas",
                size=12,
            ),
            corner_radius=10,
            border_width=1,
            border_color=COLORS["card_border"],
            state="disabled",
            wrap="word",
        )

        self.log_console.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=(0, 8),
        )

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    def _build_results_tab(self) -> None:
        tab = self.tab_results

        header = ctk.CTkFrame(
            tab,
            fg_color="transparent",
        )

        header.pack(
            fill="x",
            padx=8,
            pady=8,
        )

        ctk.CTkLabel(
            header,
            text="Таблица результатов",
            font=ctk.CTkFont(
                size=16,
                weight="bold",
            ),
        ).pack(
            side="left",
        )

        self.save_report_button = ctk.CTkButton(
            header,
            text="Сохранить отчёт как...",
            width=180,
            height=34,
            corner_radius=8,
            fg_color=COLORS["success"],
            hover_color=(COLORS["success_hover"]),
            state="disabled",
            command=self._save_report,
        )

        self.save_report_button.pack(
            side="right",
            padx=10,
        )

        self.results_count_label = ctk.CTkLabel(
            header,
            text="Записей: 0",
            text_color="gray",
        )

        self.results_count_label.pack(
            side="right",
            padx=10,
        )

        table_frame = ctk.CTkFrame(
            tab,
            corner_radius=10,
            fg_color=COLORS["card_bg"],
            border_width=1,
            border_color=COLORS["card_border"],
        )

        table_frame.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=(0, 8),
        )

        style = ttk.Style()

        style.theme_use("clam")

        style.configure(
            "Wordstat.Treeview",
            background=COLORS["card_bg"],
            foreground=COLORS["fg"],
            fieldbackground=COLORS["card_bg"],
            font=("Segoe UI", 11),
            rowheight=34,
            borderwidth=0,
        )

        style.configure(
            "Wordstat.Treeview.Heading",
            background=COLORS["table_header"],
            foreground="white",
            font=(
                "Segoe UI",
                11,
                "bold",
            ),
            borderwidth=0,
            relief="flat",
        )

        style.map(
            "Wordstat.Treeview",
            background=[
                ("selected", COLORS["accent"]),
            ],
        )

        self.tree = ttk.Treeview(
            table_frame,
            columns=(
                "query",
                "normal",
                "quoted",
            ),
            show="headings",
            style="Wordstat.Treeview",
        )

        self.tree.heading(
            "query",
            text="Запрос",
            anchor="w",
        )

        self.tree.heading(
            "normal",
            text="Показов (обычный)",
            anchor="center",
        )

        self.tree.heading(
            "quoted",
            text="Показов (в кавычках)",
            anchor="center",
        )

        self.tree.column(
            "query",
            width=450,
            anchor="w",
        )

        self.tree.column(
            "normal",
            width=180,
            anchor="center",
        )

        self.tree.column(
            "quoted",
            width=180,
            anchor="center",
        )

        self.tree.tag_configure(
            "oddrow",
            background=COLORS["table_row_odd"],
        )

        self.tree.tag_configure(
            "evenrow",
            background=COLORS["table_row_even"],
        )

        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.tree.yview,
        )

        self.tree.configure(
            yscrollcommand=scrollbar.set,
        )

        self.tree.pack(
            side="left",
            fill="both",
            expand=True,
            padx=4,
            pady=4,
        )

        scrollbar.pack(
            side="right",
            fill="y",
            pady=4,
        )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _build_settings_tab(self) -> None:
        tab = self.tab_settings

        frame = ctk.CTkFrame(
            tab,
            corner_radius=12,
            fg_color=COLORS["card_bg"],
            border_width=1,
            border_color=COLORS["card_border"],
        )

        frame.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=8,
        )

        ctk.CTkLabel(
            frame,
            text="Глобальные настройки парсинга",
            font=ctk.CTkFont(
                size=16,
                weight="bold",
            ),
        ).pack(
            anchor="w",
            padx=20,
            pady=(20, 10),
        )

        grid = ctk.CTkFrame(
            frame,
            fg_color="transparent",
        )

        grid.pack(
            padx=20,
            fill="x",
        )

        grid.columnconfigure(
            1,
            weight=1,
        )

        settings = self.config.settings

        self._add_setting_row(
            grid,
            0,
            "Лимит запросов в час:",
            "max_requests",
            settings.max_requests_per_hour,
        )

        self._add_setting_row(
            grid,
            1,
            "Задержка между запросами (сек):",
            "request_delay",
            settings.request_delay,
        )

        ctk.CTkLabel(
            frame,
            text="Пороги фильтрации",
            font=ctk.CTkFont(
                size=14,
                weight="bold",
            ),
        ).pack(
            anchor="w",
            padx=20,
            pady=(20, 5),
        )

        self._add_setting_row(
            grid,
            2,
            "Мин. показов обычного запроса:",
            "min_normal",
            settings.min_normal_count,
        )

        self._add_setting_row(
            grid,
            3,
            "Мин. показов запроса в кавычках:",
            "min_quoted",
            settings.min_quoted_count,
        )

        ctk.CTkButton(
            frame,
            text="Сохранить настройки",
            fg_color=COLORS["success"],
            hover_color=COLORS["success_hover"],
            command=self._save_settings,
        ).pack(
            anchor="w",
            padx=20,
            pady=20,
        )

    def _add_setting_row(
        self,
        parent,
        row: int,
        label: str,
        key: str,
        value,
    ) -> None:
        ctk.CTkLabel(
            parent,
            text=label,
        ).grid(
            row=row,
            column=0,
            sticky="w",
            pady=8,
            padx=(0, 10),
        )

        entry = PasteableEntry(
            parent,
            width=180,
            fg_color=COLORS["entry_bg"],
        )

        entry.insert(
            0,
            str(value),
        )

        entry.grid(
            row=row,
            column=1,
            sticky="w",
            pady=8,
        )

        setattr(
            self,
            f"entry_{key}",
            entry,
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _browse_queries(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Выберите файл с запросами",
            filetypes=[
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
        )

        if path:
            self.queries_path_var.set(path)

    def _open_account_dialog(self) -> None:
        AccountDialog(
            self.root,
            self.config,
        )

    def _start_parsing(self) -> None:
        queries_file = self.queries_path_var.get().strip()

        if not queries_file:
            messagebox.showerror(
                "Ошибка",
                "Выберите файл с запросами.",
                parent=self.root,
            )
            return

        if not os.path.isfile(queries_file):
            messagebox.showerror(
                "Ошибка",
                "Указанный файл не существует.",
                parent=self.root,
            )
            return

        if not self.config.accounts:
            messagebox.showerror(
                "Ошибка",
                "Добавьте хотя бы один аккаунт.",
                parent=self.root,
            )
            return

        self._prepare_for_start()

        self.worker = WordstatProcessor(
            config=self.config,
            queries_file=queries_file,
            log_callback=self._queue_log,
            progress_callback=self._on_progress,
            stats_callback=self._on_stats,
            result_callback=self._on_result,
            finish_callback=self._on_finish,
        )

        threading.Thread(
            target=self.worker.run,
            daemon=True,
            name="wordstat-worker",
        ).start()

    def _prepare_for_start(self) -> None:
        self.start_button.configure(
            state="disabled",
        )

        self.stop_button.configure(
            state="normal",
        )

        self.save_report_button.configure(
            state="disabled",
        )

        self.progress_bar.set(0)

        self.progress_label.configure(
            text="0 / 0",
        )

        self.status_var.set(
            "Запуск парсинга...",
        )

        self.results.clear()

        self._clear_results_table()

        self._queue_log("Запуск парсинга...")

    def _stop_parsing(self) -> None:
        if self.worker is None:
            return

        self.worker.stop()

        self.stop_button.configure(
            state="disabled",
        )

        self.status_var.set(
            "Остановка...",
        )

        self._queue_log("Отправлен сигнал остановки.")

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------

    def _on_progress(
        self,
        current: int,
        total: int,
    ) -> None:
        self.root.after(
            0,
            self._apply_progress,
            current,
            total,
        )

    def _apply_progress(
        self,
        current: int,
        total: int,
    ) -> None:
        progress = current / total if total > 0 else 0

        self.progress_bar.set(
            progress,
        )

        self.progress_label.configure(
            text=f"{current} / {total}",
        )

        self.status_var.set(
            f"Обработка: {current}/{total}",
        )

        self.card_processed.value_label.configure(
            text=f"{current} / {total}",
        )

    def _on_stats(
        self,
        remaining: int,
        account_index: int,
        account_total: int,
    ) -> None:
        self.root.after(
            0,
            self._apply_stats,
            remaining,
            account_index,
            account_total,
        )

    def _apply_stats(
        self,
        remaining: int,
        account_index: int,
        account_total: int,
    ) -> None:
        self.card_remaining.title_label.configure(
            text=(f"ОСТАЛОСЬ ЗАПРОСОВ (АКК. {account_index}/{account_total})"),
        )

        self.card_remaining.value_label.configure(
            text=str(remaining),
        )

    def _on_result(
        self,
        result: ParseResult,
        found_count: int,
    ) -> None:
        """
        Receives the result from the background thread
        and schedules a UI update on the main thread.
        """
        self.root.after(
            0,
            self._apply_result,
            result,
            found_count,
        )

    def _apply_result(
        self,
        result: ParseResult,
        found_count: int,
    ) -> None:
        """
        Updates the 'Found' card, table, and record counter in real-time.
        """
        # 1. Save result to local list (in case the user stops parsing early)
        self.results.append(result)

        # 2. Update the 'Found' (Найдено) stats card
        self.card_found.value_label.configure(
            text=str(found_count),
        )

        # 3. Add a new row to the results table
        # We use odd/even tags for alternating row colors
        tag = "oddrow" if (found_count % 2 != 0) else "evenrow"

        self.tree.insert(
            "",
            "end",
            values=(
                result.query,
                result.normal_count,
                result.quoted_count,
            ),
            tags=(tag,),
        )

        # 4. Update the text counter above the table ("Записей: X")
        self.results_count_label.configure(
            text=f"Записей: {found_count}",
        )

    def _on_finish(
        self,
        results: list[ParseResult],
    ) -> None:
        self.root.after(
            0,
            self._apply_finish,
            results,
        )

    def _apply_finish(
        self,
        results: list[ParseResult],
    ) -> None:
        self.results = list(results)

        self.start_button.configure(
            state="normal",
        )

        self.stop_button.configure(
            state="disabled",
        )

        self._refresh_results_table()

        count = len(self.results)

        self.card_found.value_label.configure(
            text=str(count),
        )

        if count:
            self.save_report_button.configure(
                state="normal",
            )

            self.status_var.set(
                f"Готово. Найдено: {count}",
            )

            self._queue_log(f"Парсинг завершён. Найдено {count} подходящих запросов.")

            self.tabview.set("Результаты")

        else:
            self.status_var.set(
                "Готово. Подходящих запросов нет.",
            )

            self._queue_log("Парсинг завершён. Подходящих запросов не найдено.")

        self.worker = None

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    def _clear_results_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.results_count_label.configure(
            text="Записей: 0",
        )

        self.card_found.value_label.configure(
            text="0",
        )

    def _refresh_results_table(self) -> None:
        self._clear_results_table()

        for index, result in enumerate(
            self.results,
        ):
            tag = "oddrow" if index % 2 else "evenrow"

            self.tree.insert(
                "",
                "end",
                values=(
                    result.query,
                    result.normal_count,
                    result.quoted_count,
                ),
                tags=(tag,),
            )

        count = len(self.results)

        self.results_count_label.configure(
            text=f"Записей: {count}",
        )

        self.card_found.value_label.configure(
            text=str(count),
        )

    def _save_report(self) -> None:
        if not self.results:
            return

        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Сохранить отчёт",
            defaultextension=".xlsx",
            initialfile="wordstat_results.xlsx",
            filetypes=[
                ("Excel files", "*.xlsx"),
                ("All files", "*.*"),
            ],
        )

        if not path:
            return

        try:
            export_to_excel(
                self.results,
                path,
            )

        except (
            OSError,
            ValueError,
        ) as error:
            messagebox.showerror(
                "Ошибка",
                (f"Не удалось сохранить отчёт:\n\n{error}"),
                parent=self.root,
            )
            return

        messagebox.showinfo(
            "Успешно",
            f"Отчёт сохранён:\n\n{path}",
            parent=self.root,
        )

        self._queue_log(f"Отчёт сохранён: {path}")

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _save_settings(self) -> None:
        try:
            max_requests = int(self.entry_max_requests.get())

            request_delay = float(self.entry_request_delay.get())

            min_normal = int(self.entry_min_normal.get())

            min_quoted = int(self.entry_min_quoted.get())

        except ValueError:
            messagebox.showerror(
                "Ошибка",
                "Введите корректные числовые значения.",
                parent=self.root,
            )
            return

        if max_requests <= 0:
            messagebox.showerror(
                "Ошибка",
                "Лимит запросов должен быть больше нуля.",
                parent=self.root,
            )
            return

        if request_delay < 0:
            messagebox.showerror(
                "Ошибка",
                "Задержка не может быть отрицательной.",
                parent=self.root,
            )
            return

        if min_normal < 0 or min_quoted < 0:
            messagebox.showerror(
                "Ошибка",
                "Пороги не могут быть отрицательными.",
                parent=self.root,
            )
            return

        self.config.settings.max_requests_per_hour = max_requests

        self.config.settings.request_delay = request_delay

        self.config.settings.min_normal_count = min_normal

        self.config.settings.min_quoted_count = min_quoted

        try:
            self.config.save()

        except OSError as error:
            messagebox.showerror(
                "Ошибка",
                (f"Не удалось сохранить настройки:\n{error}"),
                parent=self.root,
            )
            return

        messagebox.showinfo(
            "Успешно",
            "Настройки сохранены.",
            parent=self.root,
        )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _queue_log(
        self,
        message: str,
    ) -> None:
        self.log_queue.put(str(message))

    def _poll_log_queue(self) -> None:
        try:
            while True:
                message = self.log_queue.get_nowait()

                self.log_console.configure(
                    state="normal",
                )

                self.log_console.insert(
                    "end",
                    message + "\n",
                )

                self.log_console.see(
                    "end",
                )

                self.log_console.configure(
                    state="disabled",
                )

        except queue.Empty:
            pass

        try:
            self.root.after(
                100,
                self._poll_log_queue,
            )
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        if self.worker is not None:
            confirmed = messagebox.askyesno(
                "Выход",
                (
                    "Парсинг ещё выполняется.\n\n"
                    "Остановить обработку и закрыть приложение?"
                ),
                parent=self.root,
            )

            if not confirmed:
                return

            self.worker.stop()

        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
