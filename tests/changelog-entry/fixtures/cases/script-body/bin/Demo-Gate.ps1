<#
.SYNOPSIS
    A demo gate, the subject of the script-parameters surface.
.DESCRIPTION
    Does nothing. Its param block and .PARAMETER help are what the gate under test compares.
.PARAMETER Path
    The path it pretends to read.
#>
param(
    [string]$Path = '.'
)

function Get-Answer {
    return $Path.Trim()
}

Get-Answer
