# 环境预检

开发环境：WSL Linux；父工作目录独立且不是 Git 仓库；目标目录在预检时不存在。

| 项目 | 实际输出 |
|---|---|
| uname -s | Linux |
| python3 --version | Python 3.12.3 |
| git --version | git version 2.43.0 |
| gh --version | 2.45.0 |
| gh auth status | 已登录，账号在发布前再次核验 |
| docker --version | Docker 29.8.2（仅客户端，不代表 daemon 可用） |
| node --version | v24.21.0（核心不依赖 Node） |

项目虚拟环境由 uv 使用 CPython 3.12.15 创建；系统 Python 版本仍为上表所示。

绝对本机路径不进入公开仓库；交付时在对话中报告。未检查或读取其他项目的私有数据。
