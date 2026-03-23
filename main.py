"""
main.py
========
入口文件。等同于 `python -m loki`。

推荐使用子命令方式：
  python main.py run       --environment local_file
  python main.py iterate   --dataset datasets/loki_cases --max-iter 3
  python main.py generate  --attack-paths datasets/attack_paths.json
  python main.py dashboard
"""

from loki.cli import main

if __name__ == "__main__":
    main()
