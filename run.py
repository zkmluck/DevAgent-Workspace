"""DevAgent-Workspace 启动入口：在 http://127.0.0.1:7860 打开工作台。"""

from ui.web_ui import create_ui

if __name__ == "__main__":
    demo = create_ui()
    demo.launch(server_name="127.0.0.1", server_port=7860)
