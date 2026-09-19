"""Generate reproducible native PNGs and a nearest-neighbor contact sheet.

Run: python -m tools.render_examples
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image
from app.core.config import validate_config
from app.core.runtime import Runtime
from app.core.models import Message, Race, Snapshot
from app.core.fonts import draw_text
from app.outputs.browser import BrowserSink


async def main():
    output = Path("docs/frames")
    output.mkdir(parents=True, exist_ok=True)
    runtime = Runtime(validate_config({}), BrowserSink())
    for name in runtime.providers: await runtime.refresh_provider(name)
    await runtime.scenario("flight","united")
    context = runtime.context()
    context.now = datetime(2026,9,12,15,24,tzinfo=timezone.utc)
    context.message = Message("GARAGE","DOOR OPEN",False)
    examples = []
    for name, module in runtime.modules.items():
        frame = module.render(context)
        frame.save(output / f"{name}.png")
        examples.append((name,frame))
    for layout in ("route","detail","minimal"):
        context.config["modules"]["flight"]["layout"] = layout
        runtime.modules["flight"].render(context).save(output/f"flight-{layout}.png")
    sheet = Image.new("RGB", (1100, ((len(examples) + 1) // 2) * 184 + 20), (17, 19, 21))
    for index,(name,frame) in enumerate(examples):
        x,y = 22+(index%2)*550, 18+(index//2)*184
        draw_text(sheet,name.replace("_"," "),x,y,(154,166,166),2)
        sheet.paste(frame.resize((512,128),Image.Resampling.NEAREST),(x,y+25))
    sheet.save("docs/frames.png")
    print(f"Exported {len(examples)} canonical module frames and 3 flight layouts to {output}")


if __name__ == "__main__": asyncio.run(main())
