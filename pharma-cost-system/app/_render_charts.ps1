# Report chart renderer (Windows GDI+ / System.Drawing)
# Purpose: render report chart PNGs on Windows without matplotlib / Pillow.
# Called by app/charts_gdi.py with a JSON spec file. No third-party dependency.
# NOTE: keep this file ASCII-only (Windows PowerShell 5.1 reads .ps1 as ANSI unless BOM).
param(
  [Parameter(Mandatory = $true)][string]$Spec,
  [Parameter(Mandatory = $true)][string]$OutDir
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Drawing

$json = Get-Content -Path $Spec -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$PALETTE = @(
  [System.Drawing.ColorTranslator]::FromHtml('#2E6FB7'),
  [System.Drawing.ColorTranslator]::FromHtml('#E08A2E'),
  [System.Drawing.ColorTranslator]::FromHtml('#4C9A6A'),
  [System.Drawing.ColorTranslator]::FromHtml('#B4524C'),
  [System.Drawing.ColorTranslator]::FromHtml('#7C6BB0'),
  [System.Drawing.ColorTranslator]::FromHtml('#5B9BD5'),
  [System.Drawing.ColorTranslator]::FromHtml('#C9A227')
)
$C_GRID  = [System.Drawing.ColorTranslator]::FromHtml('#DDDDDD')
$C_AXIS  = [System.Drawing.ColorTranslator]::FromHtml('#BBBBBB')
$C_TEXT  = [System.Drawing.ColorTranslator]::FromHtml('#333333')
$C_TITLE = [System.Drawing.ColorTranslator]::FromHtml('#1F4E79')
$C_MUTED = [System.Drawing.ColorTranslator]::FromHtml('#666666')
$C_UP    = [System.Drawing.ColorTranslator]::FromHtml('#C0504D')
$C_DOWN  = [System.Drawing.ColorTranslator]::FromHtml('#4C9A6A')
$C_BARL  = [System.Drawing.ColorTranslator]::FromHtml('#E8EEF6')
$C_TOTAL = [System.Drawing.ColorTranslator]::FromHtml('#5B7DB1')

function New-Font([single]$size, [bool]$bold = $false) {
  $style = if ($bold) { [System.Drawing.FontStyle]::Bold } else { [System.Drawing.FontStyle]::Regular }
  foreach ($name in @('Microsoft YaHei', 'SimHei', 'SimSun', 'Arial')) {
    try { return New-Object System.Drawing.Font($name, $size, $style, [System.Drawing.GraphicsUnit]::Pixel) }
    catch { }
  }
  return New-Object System.Drawing.Font('Arial', $size, $style, [System.Drawing.GraphicsUnit]::Pixel)
}

function New-Brush([System.Drawing.Color]$c) { return New-Object System.Drawing.SolidBrush($c) }
function New-Pen([System.Drawing.Color]$c, [single]$w = 1) { return New-Object System.Drawing.Pen($c, $w) }

function Draw-Text($g, [string]$text, [single]$x, [single]$y, [single]$size, $color, [string]$anchor = 'lt', [bool]$bold = $false) {
  if ([string]::IsNullOrEmpty($text)) { return }
  $font = New-Font $size $bold
  $brush = New-Brush $color
  $m = $g.MeasureString($text, $font)
  $dx = 0.0; $dy = 0.0
  switch ($anchor.Substring(0, 1)) {
    'c' { $dx = -$m.Width / 2 }
    'r' { $dx = -$m.Width }
  }
  switch ($anchor.Substring(1, 1)) {
    'm' { $dy = -$m.Height / 2 }
    'b' { $dy = -$m.Height }
  }
  $g.DrawString($text, $font, $brush, [single]($x + $dx), [single]($y + $dy))
  $font.Dispose(); $brush.Dispose()
}

function Get-Ticks([double]$max, [int]$count = 4) {
  if ($max -le 0) { $max = 1 }
  $raw = $max / $count
  $mag = [Math]::Pow(10, [Math]::Floor([Math]::Log10($raw)))
  $step = 10 * $mag
  foreach ($m in @(1, 2, 2.5, 5, 10)) {
    if ($raw -le $m * $mag) { $step = $m * $mag; break }
  }
  $hi = [Math]::Ceiling($max / $step) * $step
  $out = @()
  for ($v = 0; $v -le $hi + $step / 1000; $v += $step) { $out += [Math]::Round($v, 6) }
  return $out
}

function Format-Num([double]$v) {
  if ([Math]::Abs($v) -ge 100) { return $v.ToString('N0') }
  if ([Math]::Abs($v) -ge 1) { return $v.ToString('0.##') }
  return $v.ToString('0.###')
}

# ------- shared: axes + grid -------
function Draw-Axes($g, $box, $ticks, $xLabels, [string]$title, [string]$ylabel) {
  $x0 = $box[0]; $y0 = $box[1]; $x1 = $box[2]; $y1 = $box[3]
  $lo = $ticks[0]; $hi = $ticks[$ticks.Count - 1]
  $rng = $hi - $lo; if ($rng -eq 0) { $rng = 1 }
  $penGrid = New-Pen $C_GRID 1
  $penAxis = New-Pen $C_AXIS 1
  foreach ($t in $ticks) {
    $y = $y1 - ($t - $lo) / $rng * ($y1 - $y0)
    $g.DrawLine($penGrid, [single]$x0, [single]$y, [single]$x1, [single]$y)
    Draw-Text $g (Format-Num $t) ($x0 - 5) $y 10.5 $C_MUTED 'rm'
  }
  $g.DrawLine($penAxis, [single]$x0, [single]$y0, [single]$x0, [single]$y1)
  $g.DrawLine($penAxis, [single]$x0, [single]$y1, [single]$x1, [single]$y1)
  if ($title) { Draw-Text $g $title $x0 12 14 $C_TITLE 'lm' $true }
  if ($ylabel) { Draw-Text $g $ylabel $x0 ($y0 - 14) 11 $C_MUTED 'lm' }
  $n = $xLabels.Count
  for ($i = 0; $i -lt $n; $i++) {
    $x = $x0 + ($i + 0.5) * ($x1 - $x0) / $n
    Draw-Text $g ([string]$xLabels[$i]) $x ($y1 + 6) 11 $C_TEXT 'cm'
  }
  $penGrid.Dispose(); $penAxis.Dispose()
}

function Draw-Legend($g, $items, [single]$x, [single]$y, [int]$ncol = 4, [single]$gap = 110) {
  for ($i = 0; $i -lt $items.Count; $i++) {
    $cx = $x + ($i % $ncol) * $gap
    $cy = $y + [Math]::Floor($i / $ncol) * 20
    $b = New-Brush $items[$i].color
    $g.FillRectangle($b, [single]$cx, [single]($cy - 4), 11, 11)
    Draw-Text $g $items[$i].name ($cx + 16) $cy 11 $C_TEXT 'lm'
    $b.Dispose()
  }
}

# ================= chart types =================
function Draw-Trend($g, $c, [int]$W, [int]$H) {
  $box = @(62, 52, ($W - 52), ($H - 40))
  $bars = @($c.bars | ForEach-Object { [double]$_ })
  $ticks = Get-Ticks (($bars | Measure-Object -Maximum).Maximum * 1.15)
  Draw-Axes $g $box $ticks $c.x $c.title $c.ylabel
  $lo = $ticks[0]; $hi = $ticks[$ticks.Count - 1]; $rng = $hi - $lo
  $x0 = $box[0]; $y0 = $box[1]; $x1 = $box[2]; $y1 = $box[3]
  $n = $bars.Count
  $bw = ($x1 - $x0) / $n * 0.52
  $bb = New-Brush $C_BARL
  for ($i = 0; $i -lt $n; $i++) {
    $cx = $x0 + ($i + 0.5) * ($x1 - $x0) / $n
    $y = $y1 - ($bars[$i] - $lo) / $rng * ($y1 - $y0)
    $g.FillRectangle($bb, [single]($cx - $bw / 2), [single]$y, [single]$bw, [single]($y1 - $y))
  }
  $bb.Dispose()
  $allv = @()
  foreach ($s in $c.series) { $allv += ($s.values | ForEach-Object { [double]$_ }) }
  $t2 = Get-Ticks ((($allv | Measure-Object -Maximum).Maximum) * 1.15)
  $lo2 = $t2[0]; $hi2 = $t2[$t2.Count - 1]; $rng2 = $hi2 - $lo2
  # secondary axis (unit cost) ticks on the right
  $penR = New-Pen $C_AXIS 1
  $g.DrawLine($penR, [single]$x1, [single]$y0, [single]$x1, [single]$y1)
  $penR.Dispose()
  foreach ($t in $t2) {
    $y = $y1 - ($t - $lo2) / $rng2 * ($y1 - $y0)
    Draw-Text $g (Format-Num $t) ($x1 + 5) $y 10.5 $C_MUTED 'lm'
  }
  if ($c.ylabel2) { Draw-Text $g $c.ylabel2 $x1 ($y0 - 14) 11 $C_MUTED 'rm' }
  $legend = @()
  for ($k = 0; $k -lt $c.series.Count; $k++) {
    $s = $c.series[$k]
    $color = $PALETTE[$k % $PALETTE.Count]
    $pen = New-Pen $color 2
    $pts = New-Object 'System.Collections.Generic.List[System.Drawing.PointF]'
    for ($i = 0; $i -lt $n; $i++) {
      $cx = $x0 + ($i + 0.5) * ($x1 - $x0) / $n
      $v = [double]$s.values[$i]
      $y = $y1 - ($v - $lo2) / $rng2 * ($y1 - $y0)
      $pts.Add((New-Object System.Drawing.PointF([single]$cx, [single]$y)))
    }
    if ($pts.Count -gt 1) { $g.DrawLines($pen, $pts.ToArray()) }
    $b = New-Brush $color
    foreach ($p in $pts) { $g.FillEllipse($b, [single]($p.X - 3), [single]($p.Y - 3), 6, 6) }
    $b.Dispose(); $pen.Dispose()
    $legend += [pscustomobject]@{ name = $s.name; color = $color }
  }
  Draw-Legend $g $legend ($x0 + 6) 26 5 118
}

function Draw-Structure($g, $c, [int]$W, [int]$H) {
  Draw-Text $g $c.title 26 14 14 $C_TITLE 'lm' $true
  $items = @($c.items)
  $total = 0.0
  foreach ($it in $items) { $total += [double]$it.value }
  if ($total -le 0) { $total = 1 }
  $cx = 140; $cy = [Math]::Floor($H / 2) + 8; $r = 88; $th = 34
  $rect = New-Object System.Drawing.RectangleF([single]($cx - $r), [single]($cy - $r), [single](2 * $r), [single](2 * $r))
  $angle = 90.0
  for ($i = 0; $i -lt $items.Count; $i++) {
    $v = [double]$items[$i].value
    $sweep = $v / $total * 360
    $start = $angle - $sweep; $end = $angle
    $pen = New-Pen $PALETTE[$i % $PALETTE.Count] $th
    $g.DrawArc($pen, $rect, [single](-$end), [single]($sweep))
    $pen.Dispose()
    $mid = [Math]::PI * ($start + $end) / 2 / 180
    $lx = $cx + [Math]::Cos($mid) * $r
    $ly = $cy - [Math]::Sin($mid) * $r
    Draw-Text $g (('{0:N1}%' -f ($v / $total * 100))) $lx $ly 10 ([System.Drawing.Color]::White) 'cm'
    $angle = $start
  }
  Draw-Text $g $c.center $cx ($cy - 6) 22 $C_TITLE 'cm' $true
  Draw-Text $g $c.center_sub $cx ($cy + 16) 11 $C_MUTED 'cm'
  for ($i = 0; $i -lt $items.Count; $i++) {
    $ly = 110 + $i * 30
    $b = New-Brush $PALETTE[$i % $PALETTE.Count]
    $g.FillRectangle($b, 280, ($ly - 6), 13, 13)
    $b.Dispose()
    Draw-Text $g (('{0} {1:N2}' -f $items[$i].name, [double]$items[$i].value)) 300 $ly 11.5 $C_TEXT 'lm'
  }
}

function Draw-Waterfall($g, $c, [int]$W, [int]$H) {
  $labels = @($c.labels); $values = @($c.values | ForEach-Object { [double]$_ }); $kinds = @($c.kinds)
  $bars = @()
  $running = 0.0
  for ($i = 0; $i -lt $values.Count; $i++) {
    if ($kinds[$i] -eq 'total') {
      $bars += [pscustomobject]@{ bottom = 0.0; height = $values[$i]; value = $values[$i]; total = $true }
      $running = $values[$i]
    } else {
      $bottom = if ($values[$i] -ge 0) { $running } else { $running + $values[$i] }
      $bars += [pscustomobject]@{ bottom = $bottom; height = [Math]::Abs($values[$i]); value = $values[$i]; total = $false }
      $running += $values[$i]
    }
  }
  $maxTop = 0.0
  foreach ($b in $bars) { $maxTop = [Math]::Max($maxTop, $b.bottom + $b.height) }
  $ticks = Get-Ticks ($maxTop * 1.18)
  $box = @(56, 44, ($W - 14), ($H - 40))
  Draw-Axes $g $box $ticks $labels $c.title $c.ylabel
  $lo = $ticks[0]; $hi = $ticks[$ticks.Count - 1]; $rng = $hi - $lo
  $x0 = $box[0]; $y0 = $box[1]; $x1 = $box[2]; $y1 = $box[3]
  $n = $bars.Count
  $bw = ($x1 - $x0) / $n * 0.5
  for ($i = 0; $i -lt $n; $i++) {
    $b = $bars[$i]
    $cx = $x0 + ($i + 0.5) * ($x1 - $x0) / $n
    $ytop = $y1 - ($b.bottom + $b.height - $lo) / $rng * ($y1 - $y0)
    $ybot = $y1 - ($b.bottom - $lo) / $rng * ($y1 - $y0)
    $color = if ($b.total) { $C_TOTAL } elseif ($b.value -ge 0) { $C_UP } else { $C_DOWN }
    $br = New-Brush $color
    $g.FillRectangle($br, [single]($cx - $bw / 2), [single]$ytop, [single]$bw, [single]($ybot - $ytop))
    $br.Dispose()
    $txt = if ($b.total) { '{0:N2}' -f $b.value } else { '{0:+0.00;-0.00}' -f $b.value }
    Draw-Text $g $txt $cx ($ytop - 4) 10.5 $C_TEXT 'cb'
  }
}

function Draw-HBar($g, $c, [int]$W, [int]$H) {
  $labels = @($c.labels); $values = @($c.values | ForEach-Object { [double]$_ })
  $amax = 0.0
  foreach ($v in $values) { $amax = [Math]::Max($amax, [Math]::Abs($v)) }
  if ($amax -le 0) { $amax = 1 }
  $amax = $amax * 1.35
  $x0 = 150; $y0 = 42; $x1 = $W - 20; $y1 = $H - 34
  $mid = ($x0 + $x1) / 2
  Draw-Text $g $c.title ($x0 - 118) 16 14 $C_TITLE 'lm' $true
  $pen = New-Pen $C_GRID 1
  foreach ($v in @((- $amax), (- $amax / 2), ($amax / 2), $amax)) {
    $x = $mid + $v / $amax * ($x1 - $x0) / 2
    $g.DrawLine($pen, [single]$x, [single]$y0, [single]$x, [single]$y1)
    Draw-Text $g ('{0:+0.00;-0.00}' -f $v) $x ($y1 + 5) 10 $C_MUTED 'ct'
  }
  $pen.Dispose()
  $penZero = New-Pen ([System.Drawing.ColorTranslator]::FromHtml('#999999')) 1
  $g.DrawLine($penZero, [single]$mid, [single]$y0, [single]$mid, [single]$y1)
  $penZero.Dispose()
  $n = $labels.Count
  $step = ($y1 - $y0) / [Math]::Max($n, 1)
  for ($i = 0; $i -lt $n; $i++) {
    $cy = $y0 + ($i + 0.5) * $step
    $v = $values[$i]
    $x = $mid + $v / $amax * ($x1 - $x0) / 2
    $br = New-Brush $(if ($v -ge 0) { $C_UP } else { $C_DOWN })
    $g.FillRectangle($br, [single][Math]::Min($mid, $x), [single]($cy - $step * 0.3), [single][Math]::Abs($x - $mid), [single]($step * 0.6))
    $br.Dispose()
    Draw-Text $g ([string]$labels[$i]) ($x0 - 6) $cy 11 $C_TEXT 'rm'
    Draw-Text $g ('{0:+0.000;-0.000}' -f $v) ($x + $(if ($v -ge 0) { 6 } else { -6 })) $cy 10.5 $C_TEXT $(if ($v -ge 0) { 'lm' } else { 'rm' })
  }
}

function Draw-Lines($g, $c, [int]$W, [int]$H) {
  $box = @(62, 54, ($W - 14), ($H - 40))
  $allv = @()
  foreach ($s in $c.series) { $allv += ($s.values | ForEach-Object { [double]$_ }) }
  $allv += 105
  $ticks = Get-Ticks ((($allv | Measure-Object -Maximum).Maximum) * 1.02)
  Draw-Axes $g $box $ticks $c.x $c.title $c.ylabel
  $lo = $ticks[0]; $hi = $ticks[$ticks.Count - 1]; $rng = $hi - $lo
  $x0 = $box[0]; $y0 = $box[1]; $x1 = $box[2]; $y1 = $box[3]
  $n = @($c.x).Count
  $penBase = New-Pen ([System.Drawing.ColorTranslator]::FromHtml('#AAAAAA')) 1
  $penBase.DashStyle = [System.Drawing.Drawing2D.DashStyle]::Dash
  $yBase = $y1 - (100 - $lo) / $rng * ($y1 - $y0)
  $g.DrawLine($penBase, [single]$x0, [single]$yBase, [single]$x1, [single]$yBase)
  $penBase.Dispose()
  $legend = @()
  for ($k = 0; $k -lt $c.series.Count; $k++) {
    $s = $c.series[$k]
    $color = $PALETTE[$k % $PALETTE.Count]
    $pen = New-Pen $color 2
    $pts = New-Object 'System.Collections.Generic.List[System.Drawing.PointF]'
    for ($i = 0; $i -lt $n; $i++) {
      $cx = $x0 + ($i + 0.5) * ($x1 - $x0) / $n
      $v = [double]$s.values[$i]
      $y = $y1 - ($v - $lo) / $rng * ($y1 - $y0)
      $pts.Add((New-Object System.Drawing.PointF([single]$cx, [single]$y)))
    }
    if ($pts.Count -gt 1) { $g.DrawLines($pen, $pts.ToArray()) }
    $b = New-Brush $color
    foreach ($p in $pts) { $g.FillEllipse($b, [single]($p.X - 3), [single]($p.Y - 3), 6, 6) }
    $b.Dispose(); $pen.Dispose()
    $legend += [pscustomobject]@{ name = $s.name; color = $color }
  }
  Draw-Legend $g $legend ($x0 + 6) 28 4 150
}

function Draw-Bars($g, $c, [int]$W, [int]$H) {
  $box = @(62, 54, ($W - 14), ($H - 40))
  $groups = @($c.groups)
  $allv = @()
  foreach ($s in $c.series) { $allv += ($s.values | ForEach-Object { [double]$_ }) }
  $ticks = Get-Ticks ((($allv | Measure-Object -Maximum).Maximum) * 1.18)
  Draw-Axes $g $box $ticks $groups $c.title $c.ylabel
  $lo = $ticks[0]; $hi = $ticks[$ticks.Count - 1]; $rng = $hi - $lo
  $x0 = $box[0]; $y0 = $box[1]; $x1 = $box[2]; $y1 = $box[3]
  $n = $groups.Count
  $gw = ($x1 - $x0) / $n
  $m = $c.series.Count
  $bw = $gw * 0.8 / $m
  $legend = @()
  for ($k = 0; $k -lt $m; $k++) {
    $s = $c.series[$k]
    $color = $PALETTE[$k % $PALETTE.Count]
    $legend += [pscustomobject]@{ name = $s.name; color = $color }
    for ($i = 0; $i -lt $n; $i++) {
      $v = [double]$s.values[$i]
      $cx = $x0 + ($i + 0.5) * $gw - ($m * $bw) / 2 + $k * $bw
      $y = $y1 - ($v - $lo) / $rng * ($y1 - $y0)
      $br = New-Brush $color
      $g.FillRectangle($br, [single]$cx, [single]$y, [single]($bw * 0.92), [single]($y1 - $y))
      $br.Dispose()
      Draw-Text $g ('{0:N2}' -f $v) ($cx + $bw * 0.46) ($y - 4) 10 $C_TEXT 'cb'
    }
  }
  if ($m -gt 1) { Draw-Legend $g $legend ($x0 + 6) 28 2 120 }
}

# ================= main loop =================
$result = @()
foreach ($c in $json.charts) {
  $W = 760; $H = 330
  if ($c.type -eq 'structure') { $W = 430; $H = 330 }
  elseif ($c.type -eq 'budget_compare' -or $c.type -eq 'bars_single') { $W = 560; $H = 300 }
  if ($c.type -eq 'hbar') {
    $H = [Math]::Max(200, 34 * @($c.labels).Count + 70)
    $W = 760
  }
  if ($c.w) { $W = [int]$c.w }
  if ($c.h) { $H = [int]$c.h }
  $bmp = New-Object System.Drawing.Bitmap($W, $H)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
  $g.Clear([System.Drawing.Color]::White)
  try {
    switch ($c.type) {
      'trend'          { Draw-Trend $g $c $W $H }
      'structure'      { Draw-Structure $g $c $W $H }
      'waterfall'      { Draw-Waterfall $g $c $W $H }
      'hbar'           { Draw-HBar $g $c $W $H }
      'lines'          { Draw-Lines $g $c $W $H }
      'bars'           { Draw-Bars $g $c $W $H }
      default          { throw "unknown chart type: $($c.type)" }
    }
    $path = Join-Path $OutDir ($c.key + '.png')
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $result += [pscustomobject]@{ key = $c.key; path = $path; ok = $true }
  } catch {
    $info = $_.InvocationInfo
    $detail = "$($_.Exception.Message) @ line $($info.ScriptLineNumber): $($info.Line.Trim())"
    $result += [pscustomobject]@{ key = $c.key; path = ''; ok = $false; error = $detail }
  } finally {
    $g.Dispose(); $bmp.Dispose()
  }
}
$result | ConvertTo-Json -Compress
