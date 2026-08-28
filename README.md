# Yandex Wordstat Parser

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![API](https://img.shields.io/badge/API-Yandex%20Wordstat-orange.svg)](https://aistudio.yandex.ru/platform/folders)
[![GUI](https://img.shields.io/badge/GUI-CustomTkinter-purple.svg)](https://github.com/TomSchimansky/CustomTkinter)

Desktop application for mass parsing search queries via the Yandex Cloud Wordstat API. Built with Python using CustomTkinter. Supports multiple accounts, automatic handling of the configured hourly request limit, threshold filtering, progress recovery, and exporting results to Excel.

![Wordstat Parser Demo](assets/video.gif)

## Installation

### Prerequisites
- Python 3.11 or higher
- `pip` package manager
- API Key and Folder ID from [Yandex Cloud](https://yandex.ru/dev/wordstat/)

### Steps

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/wordstat-parser.git
   cd wordstat-parser
   ```
1. **Install the package in editable mode**
   ```bash
      pip install -e .
      pip install -e ".[dev]"
   ```

### Usage
Launch the app from anywhere in your terminal:
   ```bash
      wordstat_parser
   ```
Or run as a module:
   ```bash
      python -m wordstat_parser
   ```

### API limits and retry behavior

The parser treats an API result and an API limit as different states:

- `HTTP 200` with `totalCount = 0` is a successful result. The query is processed normally and the value `0` is preserved.
- `HTTP 200` with `totalCount > 0` is also a successful result.
- `HTTP 400` is treated by this application as an exhausted request limit. The current query is not marked as processed and `0` is not substituted for the missing result.
- When another configured account is available, the parser switches to it and retries the same query.
- When all accounts are exhausted, the parser waits for the hourly limit reset without sending API requests, then retries the same query.
- If processing is stopped or a query cannot be completed, its index is not advanced, so the saved progress can resume from that query.

The application-level hourly limit is configured by `settings.max_requests_per_hour` in `config.json`. The Yandex Cloud Search API documentation currently lists a Wordstat limit of 100 statistics requests per hour and 10 requests per second.

### Running Tests
```bash
   pytest tests/
```

### Project Structure

```bash
wordstat_parser/
├── src/wordstat_parser/          # Main package
│   ├── __init__.py
│   ├── __main__.py               # Entry point for `python -m ...`
│   ├── ui.py                     # UI layer (CustomTkinter GUI)
│   ├── client.py                 # Yandex Wordstat API client + account management
│   ├── processor.py              # Background query processing and business logic
│   ├── exporter.py               # Results export to Excel (.xlsx)
│   ├── config.py                 # Configuration manager and global settings
│   └── models.py                 # Dataclass models (accounts, results, settings)
├── tests/                        # Unit tests
├── images/                       # App assets (screenshots, icons)
│   └── demo.gif
├── requirements.txt              # Project dependencies
├── config.json                   # Local settings and accounts (auto-generated)
├── block_history.json            # API temporary block history (auto-generated)
└── README.md
```
