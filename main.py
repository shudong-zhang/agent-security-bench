"""
main.py
========
入口文件。等同于 `python -m loki_harness`。

推荐使用子命令方式：
  python main.py blueprint
  python main.py scaffold-run --task-name "Agent security test"
  python main.py run-task --task-file runs/<run>/task.json
  python main.py export-training-views --runs-root runs --output-dir exports/views
"""

from loki_harness.cli import main

if __name__ == "__main__":
    main()
