# Pyright Type Checking Setup

This repository is configured for type checking with [Pyright](https://github.com/microsoft/pyright), Microsoft's static type checker for Python.

## Quick Start

### Run Type Checking

```bash
# Run pyright via tox
.venv/bin/tox -e pyright

# Or run pyright directly
.venv/bin/pyright custom_components/nwp500
```

### Run Both Type Checkers

```bash
# Run mypy and pyright
.venv/bin/tox -e mypy,pyright

# Or separately
.venv/bin/tox -e mypy
.venv/bin/tox -e pyright
```

## Configuration

### pyrightconfig.json

The main pyright configuration file is `pyrightconfig.json` at the repository root.

**Key Settings:**
- Python version: 3.12 (matches Home Assistant requirements)
- Type checking mode: `basic` (balanced strictness)
- Missing imports: `none` (ignores missing Home Assistant stubs)
- Virtual environment: `.venv` (automatically detected)

### Type Checking Mode

Current mode: **basic**

- Provides good type safety without being overly strict
- Suitable for Home Assistant custom component development
- Can be upgraded to `standard` or `strict` for more rigorous checking

## Integration with Editors

### VS Code

Pyright is built into the [Pylance extension](https://marketplace.visualstudio.com/items?itemName=ms-python.vscode-pylance) for VS Code.

1. Install Pylance extension
2. Settings are automatically read from `pyrightconfig.json`
3. Type errors show inline in the editor

### Other Editors

- **Vim/Neovim**: Use [pyright language server](https://github.com/microsoft/pyright/blob/main/docs/language-server.md)
- **Emacs**: Use [lsp-pyright](https://github.com/emacs-lsp/lsp-pyright)
- **Sublime Text**: Use [LSP-pyright](https://github.com/sublimelsp/LSP-pyright)

## Current Status

Running `tox -e pyright` shows:
- **2 errors** - False positives from Home Assistant's ConfigFlow syntax
- **17 warnings** - Mostly optional member access (expected for MQTT client)
- **0 informational messages**

### Known Issues

1. **ConfigFlow domain parameter** (config_flow.py:44)
   - `domain=DOMAIN` syntax is valid Home Assistant code
   - Pyright doesn't understand Home Assistant's metaclass magic
   - Can be safely ignored

2. **Optional MQTT client warnings** (coordinator.py)
   - `mqtt_client` is conditionally initialized
   - Warnings are expected and safe
   - All accesses are properly guarded with `if` checks

## Why Both Mypy and Pyright?

Using both type checkers provides complementary benefits:

### Mypy
- **Mature**: Industry standard, well-tested
- **Plugins**: Supports mypy plugins for frameworks
- **Gradual typing**: Excellent for incremental adoption
- **Configuration**: Very flexible per-module settings

### Pyright
- **Fast**: Significantly faster than mypy
- **IDE integration**: Built into VS Code/Pylance
- **Modern**: Better support for newer Python features
- **Errors**: Often catches different issues than mypy

## Continuous Integration

Both type checkers can be run in CI:

```yaml
- name: Type check with mypy
  run: tox -e mypy

- name: Type check with pyright
  run: tox -e pyright
```

## Troubleshooting

### "Import X could not be resolved"

This is expected for Home Assistant imports. The configuration sets `reportMissingImports: "none"` to suppress these.

### "X is not a known attribute of None"

These warnings appear when accessing optional attributes. They're expected and safe when properly guarded with `if` checks.

### Pyright is too strict

You can adjust strictness in `pyrightconfig.json`:
- Change `"typeCheckingMode"` to `"basic"` (less strict)
- Disable specific checks by setting them to `"none"` or `false`

### Pyright is not strict enough

For stricter checking:
- Change `"typeCheckingMode"` to `"standard"` or `"strict"`
- Enable additional report options in `pyrightconfig.json`

## Resources

- [Pyright Documentation](https://github.com/microsoft/pyright/blob/main/docs/configuration.md)
- [Type Checking Mode](https://github.com/microsoft/pyright/blob/main/docs/configuration.md#type-checking-modes)
- [Pyright vs Mypy](https://github.com/microsoft/pyright/blob/main/docs/mypy-comparison.md)
