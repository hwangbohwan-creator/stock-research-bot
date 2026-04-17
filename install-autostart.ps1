# 주식리서치 봇 자동시작 설치 스크립트 (Windows Task Scheduler)
# 사용법: powershell -ExecutionPolicy Bypass -File install-autostart.ps1 [install|uninstall|status]
# 컴퓨터 재부팅 → 사용자 로그온 시 봇이 자동으로 실행됩니다.

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding           = [System.Text.Encoding]::UTF8

$TaskName = 'StockResearchBot'
$BotDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$BotPs1   = Join-Path $BotDir 'bot.ps1'

function Install-Task {
    if (-not (Test-Path $BotPs1)) {
        Write-Host "[오류] bot.ps1 을 찾을 수 없습니다: $BotPs1"
        return
    }

    # 이미 있으면 먼저 삭제
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[기존 작업 제거됨]"
    }

    $action = New-ScheduledTaskAction `
        -Execute 'powershell.exe' `
        -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$BotPs1`" start" `
        -WorkingDirectory $BotDir

    # 로그온 시 실행 + 30초 딜레이(네트워크 안정화 대기)
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Delay = 'PT30S'

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    $principal = New-ScheduledTaskPrincipal `
        -UserId $env:USERNAME `
        -LogonType Interactive `
        -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description '주식리서치 텔레그램 봇 자동 시작 (로그온 시 실행)' | Out-Null

    Write-Host "[등록 완료] 작업 이름: $TaskName"
    Write-Host "[동작] 로그온 30초 후 bot.ps1 start 자동 실행"
    Write-Host "[확인] 제어판 → 작업 스케줄러 → 작업 스케줄러 라이브러리 → $TaskName"
}

function Uninstall-Task {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Host "[등록되어 있지 않음]"
        return
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[제거 완료] $TaskName"
}

function Status-Task {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Host "[미등록] 자동시작이 설치되어 있지 않습니다."
        Write-Host "설치: powershell -ExecutionPolicy Bypass -File install-autostart.ps1 install"
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "[등록됨] $TaskName"
    Write-Host "  상태:        $($existing.State)"
    Write-Host "  마지막 실행: $($info.LastRunTime)"
    Write-Host "  마지막 결과: $($info.LastTaskResult)"
    Write-Host "  다음 실행:   $($info.NextRunTime)"
}

$cmd = if ($args.Count -gt 0) { $args[0] } else { 'install' }
switch ($cmd) {
    'install'   { Install-Task }
    'uninstall' { Uninstall-Task }
    'status'    { Status-Task }
    default {
        Write-Host "사용법: powershell -ExecutionPolicy Bypass -File install-autostart.ps1 {install|uninstall|status}"
    }
}
