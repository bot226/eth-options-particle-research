Set WshShell = CreateObject("WScript.Shell")
' 0 = Hide the window, False = don't wait for completion
WshShell.Run "cmd /c start_dashboard.bat", 0, False
Set WshShell = Nothing
