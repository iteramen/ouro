<#
.SYNOPSIS
    A script outside bin/, which is none of the surfaces.
.DESCRIPTION
    Its param block moves in the outside-the-surfaces case, and nothing reports it.
.PARAMETER Name
    A name.
.PARAMETER Loud
    Say it twice.
#>
param(
    [string]$Name = 'demo',
    [switch]$Loud
)

$Name
