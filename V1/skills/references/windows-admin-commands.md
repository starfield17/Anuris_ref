# Windows Admin Commands

Run with Administrator permission.

## Security profile presets (non-interactive)

```bat
cmd /c skills\scripts\windows\windows_security_unlock_cli.bat status
cmd /c skills\scripts\windows\windows_security_unlock_cli.bat level1
cmd /c skills\scripts\windows\windows_security_unlock_cli.bat level2
cmd /c skills\scripts\windows\windows_security_unlock_cli.bat level3
cmd /c skills\scripts\windows\windows_security_unlock_cli.bat restore
```

## Windows package mirror switching

```bat
cmd /c skills\scripts\source\switch_source_cli.bat show
cmd /c skills\scripts\source\switch_source_cli.bat pip tsinghua
cmd /c skills\scripts\source\switch_source_cli.bat conda ustc
```
