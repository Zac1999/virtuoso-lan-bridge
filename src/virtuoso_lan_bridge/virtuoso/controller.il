; Load in the controller computer's Virtuoso CIW.
; This attaches the existing Bridge; scheduling runs in the Windows controller.
let((setup)
  if(boundp('RBIpc) && ipcIsAliveProcess(RBIpc) then
    printf("VLB controller: using the running Virtuoso Bridge. Verify 1+2 from the controller app.\n")
  else
    setup = getShellEnvVar("VLB_BRIDGE_SETUP")
    unless(setup
      setup = strcat(getShellEnvVar("HOME") "/.cache/virtuoso_lan_bridge/active_setup.il")
    )
    if(isFile(setup) then
      load(setup)
      printf("VLB controller: setup loaded. Verify 1+2 from the controller app.\n")
    else
      error("VLB: install the node Bridge first, or set VLB_BRIDGE_SETUP to the existing setup.il path.\n")
    )
  )
)
