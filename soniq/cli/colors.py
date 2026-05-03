"""
CLI Color and Styling Utilities
Provides colored output and terminal styling for Soniq CLI commands.
"""

import os
import sys
from typing import Optional


class Colors:
    """ANSI color codes for terminal output."""

    # Reset
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # Background colors
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"


def supports_color() -> bool:
    """
    Check if the terminal supports color output.

    Returns:
        True if color is supported, False otherwise
    """
    # Check if NO_COLOR environment variable is se
    if os.environ.get("NO_COLOR"):
        return False

    # Check if FORCE_COLOR is se
    if os.environ.get("FORCE_COLOR"):
        return True

    # Check if output is a TTY
    if not hasattr(sys.stdout, "isatty"):
        return False

    if not sys.stdout.isatty():
        return False

    # Check TERM environment variable
    term = os.environ.get("TERM", "").lower()
    if term in ("dumb", "unknown"):
        return False

    return True


def colorize(text: str, color: str, bold: bool = False) -> str:
    """
    Colorize text if color is supported.

    Args:
        text: Text to colorize
        color: Color code from Colors class
        bold: Whether to make text bold

    Returns:
        Colorized text or plain text if color not supported
    """
    if not supports_color():
        return text

    prefix = color
    if bold:
        prefix = Colors.BOLD + color

    return f"{prefix}{text}{Colors.RESET}"


def success(text: str, bold: bool = False) -> str:
    """Format text as success message (green)."""
    return colorize(text, Colors.GREEN, bold)


def error(text: str, bold: bool = True) -> str:
    """Format text as error message (red)."""
    return colorize(text, Colors.RED, bold)


def warning(text: str, bold: bool = False) -> str:
    """Format text as warning message (yellow)."""
    return colorize(text, Colors.YELLOW, bold)


def info(text: str, bold: bool = False) -> str:
    """Format text as info message (blue)."""
    return colorize(text, Colors.BLUE, bold)


def highlight(text: str, bold: bool = True) -> str:
    """Format text as highlighted (cyan)."""
    return colorize(text, Colors.CYAN, bold)


def dim(text: str) -> str:
    """Format text as dimmed."""
    if not supports_color():
        return text
    return f"{Colors.DIM}{text}{Colors.RESET}"


def bold(text: str) -> str:
    """Format text as bold."""
    if not supports_color():
        return text
    return f"{Colors.BOLD}{text}{Colors.RESET}"


class StatusIcon:
    """Status icons for CLI output."""

    @staticmethod
    def success() -> str:
        """Success icon."""
        return success("✓", bold=True) if supports_color() else "[OK]"

    @staticmethod
    def error() -> str:
        """Error icon."""
        return error("✗", bold=True) if supports_color() else "[ERROR]"

    @staticmethod
    def warning() -> str:
        """Warning icon."""
        return warning("⚠", bold=True) if supports_color() else "[WARNING]"

    @staticmethod
    def info() -> str:
        """Info icon."""
        return info("ℹ", bold=True) if supports_color() else "[INFO]"

    @staticmethod
    def loading() -> str:
        """Loading icon."""
        return highlight("⏳", bold=True) if supports_color() else "[LOADING]"

    @staticmethod
    def rocket() -> str:
        """Rocket icon."""
        return highlight("🚀") if supports_color() else ">>>"

    @staticmethod
    def workers() -> str:
        """Workers icon."""
        return highlight("👥") if supports_color() else "[WORKERS]"


def print_header(title: str, width: int = 60):
    """Print a styled header."""
    if supports_color():
        print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * width}{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}{title.center(width)}{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}{'=' * width}{Colors.RESET}\n")
    else:
        print(f"\n{'=' * width}")
        print(f"{title.center(width)}")
        print(f"{'=' * width}\n")


def print_section(title: str):
    """Print a section header."""
    if supports_color():
        print(f"\n{Colors.BOLD}{Colors.BLUE}{title}{Colors.RESET}")
        print(f"{Colors.DIM}{'-' * len(title)}{Colors.RESET}")
    else:
        print(f"\n{title}")
        print(f"{'-' * len(title)}")


def print_status(message: str, status: str = "info", prefix: str = ""):
    """
    Print a status message with appropriate styling.

    Args:
        message: Message to print
        status: Status type (success, error, warning, info)
        prefix: Optional prefix text
    """
    icon_map = {
        "success": StatusIcon.success(),
        "error": StatusIcon.error(),
        "warning": StatusIcon.warning(),
        "info": StatusIcon.info(),
        "loading": StatusIcon.loading(),
    }

    icon = icon_map.get(status, StatusIcon.info())

    if prefix:
        prefix_text = dim(f"[{prefix}] ")
    else:
        prefix_text = ""

    print(f"{prefix_text}{icon} {message}")


def print_table(headers: list, rows: list, max_width: int = 100):
    """
    Print a simple table with proper alignment.

    Args:
        headers: List of column headers
        rows: List of row data (list of lists)
        max_width: Maximum table width
    """
    if not rows:
        print(dim("No data to display"))
        return

    # Calculate column widths
    col_widths = []
    for i, header in enumerate(headers):
        max_width_col = len(header)
        for row in rows:
            if i < len(row):
                max_width_col = max(max_width_col, len(str(row[i])))
        col_widths.append(min(max_width_col, max_width // len(headers)))

    # Print header
    header_row = " | ".join(
        header.ljust(col_widths[i])[: col_widths[i]] for i, header in enumerate(headers)
    )
    if supports_color():
        print(f"{Colors.BOLD}{header_row}{Colors.RESET}")
        print(f"{Colors.DIM}{'-' * len(header_row)}{Colors.RESET}")
    else:
        print(header_row)
        print("-" * len(header_row))

    # Print rows
    for row in rows:
        row_data = []
        for i, cell in enumerate(row):
            if i < len(col_widths):
                cell_str = str(cell).ljust(col_widths[i])[: col_widths[i]]
                row_data.append(cell_str)
        print(" | ".join(row_data))


def print_progress_bar(
    current: int, total: int, width: int = 50, prefix: str = "Progress"
):
    """
    Print a progress bar.

    Args:
        current: Current progress value
        total: Total progress value
        width: Width of progress bar
        prefix: Prefix text
    """
    if total == 0:
        percentage = 100
        filled_width = width
    else:
        percentage = min(100, (current / total) * 100)  # type: ignore[assignment]
        filled_width = int(width * current / total)

    if supports_color():
        bar = f"{Colors.GREEN}{'█' * filled_width}{Colors.DIM}{'░' * (width - filled_width)}{Colors.RESET}"
        print(
            f"\r{prefix}: {bar} {percentage:.1f}% ({current}/{total})",
            end="",
            flush=True,
        )
    else:
        bar = f"{'#' * filled_width}{'-' * (width - filled_width)}"
        print(
            f"\r{prefix}: [{bar}] {percentage:.1f}% ({current}/{total})",
            end="",
            flush=True,
        )


def print_key_value(key: str, value: str, indent: int = 0):
    """
    Print a key-value pair with styling.

    Args:
        key: Key text
        value: Value text
        indent: Indentation level
    """
    indent_str = "  " * indent

    if supports_color():
        print(f"{indent_str}{Colors.CYAN}{key}:{Colors.RESET} {value}")
    else:
        print(f"{indent_str}{key}: {value}")


def print_list(items: list, bullet: str = "•", indent: int = 0):
    """
    Print a styled list.

    Args:
        items: List items to print
        bullet: Bullet character
        indent: Indentation level
    """
    indent_str = "  " * indent

    for item in items:
        if supports_color():
            print(f"{indent_str}{Colors.DIM}{bullet}{Colors.RESET} {item}")
        else:
            print(f"{indent_str}{bullet} {item}")


def confirm(message: str, default: bool = False) -> bool:
    """
    Show a confirmation prompt with styling.

    Args:
        message: Confirmation message
        default: Default value if user just presses enter

    Returns:
        True if confirmed, False otherwise
    """
    suffix = " [Y/n]" if default else " [y/N]"

    if supports_color():
        prompt = f"{Colors.YELLOW}?{Colors.RESET} {message}{dim(suffix)}: "
    else:
        prompt = f"? {message}{suffix}: "

    try:
        response = input(prompt).strip().lower()

        if not response:
            return default

        return response in ("y", "yes", "true", "1")

    except (KeyboardInterrupt, EOFError):
        print()  # New line after interrupt
        return False


def prompt(message: str, default: Optional[str] = None) -> str:
    """
    Show an input prompt with styling.

    Args:
        message: Prompt message
        default: Default value if user just presses enter

    Returns:
        User input or default value
    """
    if default:
        suffix = f" ({dim(default)})"
    else:
        suffix = ""

    if supports_color():
        prompt_text = f"{Colors.BLUE}?{Colors.RESET} {message}{suffix}: "
    else:
        prompt_text = f"? {message}{suffix}: "

    try:
        response = input(prompt_text).strip()
        return response if response else (default or "")

    except (KeyboardInterrupt, EOFError):
        print()  # New line after interrupt
        return default or ""
