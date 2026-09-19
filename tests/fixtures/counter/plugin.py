import os

from rackticker import Module, Plugin, Provider, Snapshot, centered, new_frame


class Count(Provider):
    def __init__(self, context):
        self.context, self.count = context, 0

    async def fetch(self):
        self.count += 1
        if os.environ.get("COUNTER_CRASH") or self.context.settings["crash"]:
            os._exit(3)
        return Snapshot(self.count)


class Screen(Module):
    name = "counter"

    def available(self, context):
        return bool(context.snapshots.get("counter"))

    def render(self, context):
        print("rendering", context.animation_time)  # must not corrupt the protocol
        frame = new_frame()
        snap = context.snapshots.get("counter")
        centered(frame, f"{context.config['plugins']['counter']['label']} {snap.data if snap else 0}", 12)
        return frame


plugin = Plugin("counter", "Counter", module=Screen, provider=Count, defaults={"label": "COUNT", "crash": False})
