@{
    RootModule        = 'AgentRemediator.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = '0ce16302-235c-4b0f-9ee5-70801c0a1027'
    Author            = 'Jose'
    Description       = 'P05 remediation agent: three fixed write functions, each with -Preview, exposed through JEA (ADR 0034).'
    PowerShellVersion = '5.1'
    FunctionsToExport = @('Set-AgentServiceConfig', 'Set-AgentAuditSetting', 'Set-AgentRegistryValue')
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
