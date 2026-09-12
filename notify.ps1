# Windows の通知（トースト）を出す。バックアップの失敗や異常を知らせるために使う。
# 使い方: powershell -ExecutionPolicy Bypass -File notify.ps1 -Title "見出し" -Message "本文"
param(
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $true)][string]$Message
)

function Show-Toast($Title, $Message) {
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
    $t = [System.Security.SecurityElement]::Escape($Title)
    $m = [System.Security.SecurityElement]::Escape($Message)
    $xml = '<toast scenario="reminder"><visual><binding template="ToastGeneric">' +
           '<text>' + $t + '</text><text>' + $m + '</text>' +
           '</binding></visual></toast>'
    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml($xml)
    $toast = New-Object Windows.UI.Notifications.ToastNotification $doc
    # スタートメニューに実体のあるアプリIDを借りる（PowerShell）
    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
}

function Show-Balloon($Title, $Message) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $ni = New-Object System.Windows.Forms.NotifyIcon
    $ni.Icon = [System.Drawing.SystemIcons]::Warning
    $ni.BalloonTipTitle = $Title
    $ni.BalloonTipText = $Message
    $ni.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Warning
    $ni.Visible = $true
    $ni.ShowBalloonTip(20000)
    Start-Sleep -Seconds 12
    $ni.Dispose()
}

try {
    Show-Toast $Title $Message
    Write-Output "toast"
    exit 0
}
catch {
    $err = $_.Exception.Message
}

# トーストが使えない環境では通知領域のバルーンに落とす
try {
    Show-Balloon $Title $Message
    Write-Output "balloon"
    exit 0
}
catch {
    Write-Output ("failed: " + $err + " / " + $_.Exception.Message)
    exit 1
}
