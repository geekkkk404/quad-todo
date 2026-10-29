# QuadTodo 便携版开机自启开关：快捷方式固定指向包内 runtime\pythonw.exe，
# 不探测系统 Python（便携版不依赖系统安装）。
$p = Join-Path ([Environment]::GetFolderPath('Startup')) 'QuadTodo.lnk'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw = Join-Path $here 'runtime\pythonw.exe'
$script = Join-Path $here 'quad_todo.pyw'
if (-not (Test-Path $pyw)) {
    Write-Host '没有找到 runtime\pythonw.exe，请保持本文件与 runtime 文件夹在同一目录。'
    exit 1
}
if (Test-Path $p) {
    Remove-Item $p
    Write-Host '已取消开机自启（启动文件夹里的 QuadTodo.lnk 已删除）'
} else {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($p)
    $s.TargetPath = $pyw
    $s.Arguments = '"' + $script + '"'
    $s.WorkingDirectory = $here
    $s.Save()
    Write-Host '已开启开机自启（启动文件夹已创建 QuadTodo.lnk）'
}
