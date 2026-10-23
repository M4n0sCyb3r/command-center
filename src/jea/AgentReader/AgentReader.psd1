@{
    # Module manifest for AgentReader (P01 Wintel Validation Agent).
    # Installed on each target under
    #   C:\Program Files\WindowsPowerShell\Modules\AgentReader\
    RootModule        = 'AgentReader.psm1'
    ModuleVersion     = '0.2.0'
    GUID              = 'e08ac5e4-ad5b-42e2-9794-17ab2fd03422'
    Author            = 'Jose'
    Description       = 'Read-only checks exposed to the P01 agent through JEA.'
    PowerShellVersion = '5.1'

    # Name every export. A wildcard here would expose whatever is added later
    # without anyone deciding to.
    FunctionsToExport = @('Get-AgentAuditPolicy', 'Get-AgentServiceState', 'Get-AgentRegistryState')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
