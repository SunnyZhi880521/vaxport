"""vaxport TUI 启动器 — 直接启动 Textual TUI

注意：vaxport-tui 入口点已直接指向 vaxport.cli:main。
此文件保留用于向后兼容的直接导入。
"""

import sys


def main():
    from vaxport.cli import main as cli_main
    sys.exit(cli_main())


if __name__ == "__main__":
    main()