!macro customInit
  StrCpy $R0 $INSTDIR 2
  StrCpy $R1 $INSTDIR "" 2
  StrCmp $R1 "\" 0 +3
    StrCpy $INSTDIR "$R0\$(^Name)"
    Goto +2
  StrCmp $R1 "" 0 +2
    StrCpy $INSTDIR "$R0\$(^Name)"
!macroend

Function .onVerifyInstDir
  StrCpy $R0 $INSTDIR 2
  StrCpy $R1 $INSTDIR "" 2
  StrCmp $R1 "\" 0 +3
    StrCpy $INSTDIR "$R0\$(^Name)"
    Goto +2
  StrCmp $R1 "" 0 +2
    StrCpy $INSTDIR "$R0\$(^Name)"
FunctionEnd
