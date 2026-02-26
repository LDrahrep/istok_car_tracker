import gspread
from google.oauth2.service_account import Credentials
from typing import List, Optional, Tuple, Any


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class SheetsClient:
    def __init__(self, credentials_json: str, spreadsheet_id: str):
        self.spreadsheet_id = spreadsheet_id

        creds = Credentials.from_service_account_file(
            credentials_json,
            scopes=SCOPES,
        )

        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(self.spreadsheet_id)

    # ---------------------------------------------------------
    # BASIC WORKSHEET ACCESS
    # ---------------------------------------------------------

    def _ws(self, name: str):
        return self.sh.worksheet(name)

    def _values(self, name: str) -> List[List[str]]:
        """ALWAYS read directly from Google Sheets. No cache."""
        ws = self._ws(name)
        return ws.get_all_values()

    # ---------------------------------------------------------
    # READ OPERATIONS
    # ---------------------------------------------------------

    def get_all(self, sheet_name: str) -> List[List[str]]:
        return self._values(sheet_name)

    def find_row_by_telegram_id(
        self, sheet_name: str, telegram_id: int
    ) -> Optional[Tuple[int, List[str]]]:
        rows = self._values(sheet_name)
        if not rows:
            return None

        headers = rows[0]
        if "telegramID" not in headers:
            return None

        tg_index = headers.index("telegramID")

        for i, row in enumerate(rows[1:], start=2):
            if tg_index < len(row):
                if str(row[tg_index]).strip() == str(telegram_id):
                    return i, row

        return None

    # ---------------------------------------------------------
    # WRITE OPERATIONS
    # ---------------------------------------------------------

    def append_row(self, sheet_name: str, row: List[Any]):
        ws = self._ws(sheet_name)
        ws.append_row(list(row))

    def update_cell(self, sheet_name: str, row: int, col: int, value: Any):
        ws = self._ws(sheet_name)
        ws.update_cell(row, col, value)

    def update_row(self, sheet_name: str, row_number: int, values: List[Any]):
        ws = self._ws(sheet_name)
        ws.update(f"A{row_number}", [list(values)])

    def clear_sheet(self, sheet_name: str):
        ws = self._ws(sheet_name)
        ws.clear()
