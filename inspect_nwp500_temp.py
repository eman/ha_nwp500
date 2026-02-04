import nwp500.temperature
import inspect

print("Exports of nwp500.temperature:")
for name, obj in inspect.getmembers(nwp500.temperature):
    if not name.startswith("_"):
        print(name)
