Headless GUI smoke test (no display needed). Uses fake tkinter/customtkinter whose widget options
are checked against the real CustomTkinter source.

    git clone --depth 1 https://github.com/TomSchimansky/CustomTkinter ctk_src
    python tests/gui/build_fakes.py ctk_src fakes
    python tests/gui/smoke.py fakes .
