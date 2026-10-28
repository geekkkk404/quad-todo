# QuadTodo 开机自启开关：启动文件夹里有快捷方式则删除（取消），没有则创建（开启）
$p = Join-Path ([Environment]::GetFolderPath('Startup')) 'QuadTodo.lnk'
if (Test-Path $p) {
    Remove-Item $p
    Write-Host '已取消开机自启（启动文件夹里的 QuadTodo.lnk 已删除）'
} else {
    $pyw = Join-Path $env:WINDIR 'pyw.exe'
    if (-not (Test-Path $pyw)) {
        $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
        if ($cmd) { $pyw = $cmd.Source }
    }
    if (-not ($pyw -and (Test-Path $pyw))) {
        Write-Host '没有找到 pyw.exe / pythonw.exe，无法创建自启，请把此输出发给 ZCode。'
        exit 1
    }
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($p)
    $s.TargetPath = $pyw
    $s.Arguments = '"' + (Join-Path $PSScriptRoot 'quad_todo.pyw') + '"'
    $s.WorkingDirectory = $PSScriptRoot
    $s.Save()
    Write-Host '已开启开机自启（启动文件夹已创建 QuadTodo.lnk）'
}
