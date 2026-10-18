<#
  PostgreSQL local sin Docker (esta máquina no tiene Docker Desktop ni WSL con distro).
  Usa los binarios portables en C:\tools\pgsql, inicializados una sola vez.

  .\scripts\postgres.ps1 start   # arranca en 127.0.0.1:5433 (trust auth, solo loopback)
  .\scripts\postgres.ps1 stop
  .\scripts\postgres.ps1 status
#>
param(
    [ValidateSet('start', 'stop', 'status')]
    [string]$Accion = 'status'
)

$PG = 'C:\tools\pgsql'
$Puerto = 5433

switch ($Accion) {
    'start' {
        & "$PG\bin\pg_ctl.exe" -D "$PG\data" -o "-p $Puerto -c listen_addresses=127.0.0.1" -l "$PG\pg.log" start
    }
    'stop' {
        & "$PG\bin\pg_ctl.exe" -D "$PG\data" -m fast stop
    }
    'status' {
        & "$PG\bin\pg_ctl.exe" -D "$PG\data" status
    }
}
