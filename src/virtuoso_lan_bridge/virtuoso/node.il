; Load in the compute computer's Virtuoso CIW.
; This attaches the existing Bridge; SSH supplies the authenticated transport.
let((setup)
  if(boundp('RBIpc) && ipcIsAliveProcess(RBIpc) then
    printf("VLB node: using the running Virtuoso Bridge. Verify 1+2 from the controller app.\n")
  else
    setup = getShellEnvVar("VLB_BRIDGE_SETUP")
    unless(setup
      setup = strcat(getShellEnvVar("HOME") "/.cache/virtuoso_lan_bridge/active_setup.il")
    )
    if(isFile(setup) then
      load(setup)
      printf("VLB node: setup loaded. Verify 1+2 from the controller app.\n")
    else
      error("VLB: install the node Bridge first, or set VLB_BRIDGE_SETUP to the existing setup.il path.\n")
    )
  )
)
