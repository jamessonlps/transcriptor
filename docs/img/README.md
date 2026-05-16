# Screenshots & demo assets

Drop the following here to wire up the main README:

- **`demo.gif`** — short clip (5-8s) of the main flow:
  drag-and-drop → segments streaming in → diarisation finishing.
  Recommended capture: 1280×720, 15 fps, < 5 MB.
  Tool suggestion: `vhs`, `peek`, or QuickTime + `gifski`.

- **`settings.png`** — single screenshot of Configurações
  showing the HF card in its "valid + all repos accessible" state.

- **`hero.png`** — first-paint of the Transcrever view, useful as a
  social card image.

Then uncomment the `<!-- ![Transcriptor — ...] -->` line at the top of the
main README to point at `docs/img/demo.gif`.

> Generation tip — minimal `vhs` tape:
>
> ```text
> Output docs/img/demo.gif
> Set Width 1280
> Set Height 720
> Set Theme "Catppuccin Mocha"
> Set Padding 0
> Type "open http://localhost:8765"
> Enter
> Sleep 2s
> ```
