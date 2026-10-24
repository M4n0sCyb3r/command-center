# MANIFEST

Built by `tools/showcase.py` from the private repo `agentic-lab` at commit `a2eb8c3ff9bd`.
The include list, the rewrite table and the allow-list are in that repo; the employer
deny-list is not in any repo by design. Every file below passed the scrub and the link check.

**271 files.** Rewrites (personal paths -> `<repo>`) applied: 43 in 21 files. Reviewed allow-list entries used: 6.

| File | sha256 (first 16) | rewrites |
|---|---|---|
| `.github/workflows/checks.yml` | `d529b77717112f08` |  |
| `.gitignore` | `791ee083ff47f13d` |  |
| `LICENSE` | `d0e94bd109f78714` |  |
| `README.md` | `5e59707a36fec282` |  |
| `bench-pin.json` | `1514ef973b54bd6e` |  |
| `data/corpus.jsonl` | `f922eb34787141ec` |  |
| `data/corpus.schema.json` | `fa97f4168f61ac73` |  |
| `data/microsoft/3-approve-and-deploy-updates-in-wsus.md` | `dfa8fc3444374b79` |  |
| `data/microsoft/4-configure-group-policy-settings-for-automatic-updates.md` | `5034459f2bc187e6` |  |
| `data/microsoft/ATTRIBUTION.md` | `ef7f5535cb872e01` |  |
| `data/microsoft/Windows-Time-Service-Tools-and-Settings.md` | `ac4dd2d6a712c20f` |  |
| `data/microsoft/Windows-Time-Service.md` | `d45408c15834d304` |  |
| `data/microsoft/bitsadmin-getcreationtime.md` | `600585e6e323d721` |  |
| `data/microsoft/bitsadmin-util-and-repairservice.md` | `2804e2c4c4f31631` |  |
| `data/microsoft/change-the-amount-of-time-that-clients-cache-referrals.md` | `3ba4331088c0f10d` |  |
| `data/microsoft/cleanmgr.md` | `af3b3c5b8a71db0d` |  |
| `data/microsoft/configure-remote-management-in-server-manager.md` | `bfda75776ff93369` |  |
| `data/microsoft/diskpart.md` | `64a5c46d59f00790` |  |
| `data/microsoft/sc-config.md` | `4cb9b200bc6de02c` |  |
| `data/microsoft/sc-query.md` | `fb1ac21d1001f557` |  |
| `data/remediation-baseline.json` | `3b073e2e6453beea` |  |
| `data/runbooks/disk-space.md` | `b8b11b94eff1c6e0` |  |
| `data/runbooks/patching-window.md` | `645f9cbbe2728c60` |  |
| `data/runbooks/service-restart.md` | `53e8c3a84ab65b6e` |  |
| `data/runbooks/time-sync.md` | `8532b37cd7380ced` |  |
| `data/runbooks/winrm-remoting.md` | `20703473205ad14d` |  |
| `docs/adr/0040-p6-what-the-command-center-builds.md` | `8c0a48ae553b3571` | 1 |
| `docs/adr/0041-p6-jobs-and-the-entry-point.md` | `493da4a32b83a2cf` |  |
| `docs/adr/0042-p6-the-promotion-flow.md` | `aabf8a75a5ea7d4e` |  |
| `docs/adr/0043-p6-models-per-role.md` | `23bcd65e139b135a` |  |
| `docs/adr/0044-p6-exit-codes-and-harness-entry.md` | `bb88deda5e5665c5` |  |
| `docs/adr/0045-p6-deliverables-and-the-public-showcase.md` | `9044cb99584e1fe2` |  |
| `docs/adr/0046-p6-bench-runtime-ollama-0408.md` | `466619d7efe65e50` |  |
| `evals/baselines/agent.json` | `b1502d90536ef689` |  |
| `evals/baselines/answer.json` | `e5db5912da1cc9ea` |  |
| `evals/baselines/command-center.json` | `71e5c5c987a6b558` |  |
| `evals/baselines/remediation.json` | `1afacc41a3492139` |  |
| `evals/baselines/retrieval.json` | `f08f785a5fc08536` |  |
| `evals/baselines/review.json` | `477da9bfa0789340` |  |
| `evals/fixtures/faithfulness-calibration.jsonl` | `f6cad67b5de37d3b` |  |
| `evals/fixtures/faithfulness-heldout-v2.jsonl` | `f126a759b2e88792` |  |
| `evals/fixtures/faithfulness-heldout-v3.jsonl` | `58b2f06bcceef9ee` |  |
| `evals/fixtures/faithfulness-heldout.jsonl` | `fc6f0a0ac49837e0` |  |
| `evals/fixtures/judge-calibration.jsonl` | `eb6009cb031792a5` |  |
| `evals/suites/agent.jsonl` | `31e1cce5036ef36c` |  |
| `evals/suites/answer-heldout-v2.jsonl` | `136face037941468` |  |
| `evals/suites/answer-heldout-v3.jsonl` | `44e595925f7786f6` |  |
| `evals/suites/answer-heldout.jsonl` | `eaeee328be6dad82` |  |
| `evals/suites/answer.jsonl` | `63919195c152cd6d` |  |
| `evals/suites/command-center.jsonl` | `6cd419e55f8b51e7` |  |
| `evals/suites/remediation.jsonl` | `a5421a2d9f1e2c07` |  |
| `evals/suites/retrieval.jsonl` | `9185781cdc708801` |  |
| `evals/suites/review.jsonl` | `0da756fb68aa1377` |  |
| `playbooks/ansible/p06-srv01.yml` | `77ab9ca436cb0c1a` |  |
| `playbooks/p06-promote-on-srv01.md` | `c15b2718d1104eba` | 3 |
| `projects/01-wintel-validation-agent/README.md` | `483b7028e4564a27` |  |
| `projects/02-ask-the-vault/README.md` | `7915541519037b22` |  |
| `projects/02-ask-the-vault/evidence/judge-readings/20261013T034428Z-calibration-faithfulness-heldout.json` | `4960081da5dba506` |  |
| `projects/02-ask-the-vault/evidence/judge-readings/20261013T040920Z-calibration-faithfulness-heldout-v2.json` | `96d5924cdeef742e` | 1 |
| `projects/02-ask-the-vault/evidence/judge-readings/20261013T041241Z-calibration-faithfulness-heldout-v2.json` | `b66cc9d4d422094b` | 1 |
| `projects/02-ask-the-vault/evidence/judge-readings/20261015T030829Z-calibration-faithfulness-calibration.json` | `e5bd7f4717d350fa` |  |
| `projects/02-ask-the-vault/evidence/judge-readings/20261015T033238Z-calibration-faithfulness-heldout-v2.json` | `39364158cd2aa422` | 1 |
| `projects/02-ask-the-vault/evidence/judge-readings/20261016T163919Z-calibration-faithfulness-heldout-v2.json` | `fd6cf3fa68dde677` |  |
| `projects/02-ask-the-vault/evidence/judge-readings/20261016T171403Z-calibration-faithfulness-heldout-v3.json` | `54b58549e4434d71` | 1 |
| `projects/03-script-review-pipeline/README.md` | `d064f6b930fda44c` |  |
| `projects/03-script-review-pipeline/evidence/pssa-defects-1.25.0.json` | `c2269c87b71c2730` |  |
| `projects/03-script-review-pipeline/evidence/pssa-reference-1.25.0.json` | `2899de044dd49b5e` |  |
| `projects/03-script-review-pipeline/rubric.md` | `be2a52695c6fb2cd` |  |
| `projects/03-script-review-pipeline/set/defects.jsonl` | `fe387ec869c687ad` |  |
| `projects/03-script-review-pipeline/set/defects/01-Start-StoppedAutoServices__01-B.ps1` | `4aaa39abcef4a5ff` |  |
| `projects/03-script-review-pipeline/set/defects/01-Start-StoppedAutoServices__01-G.ps1` | `8b76b18547d373d4` |  |
| `projects/03-script-review-pipeline/set/defects/01-Start-StoppedAutoServices__01-L.ps1` | `b513e1d68e68697e` |  |
| `projects/03-script-review-pipeline/set/defects/02-Clear-OldLogFiles__02-B.ps1` | `72a1ae472cec44ea` |  |
| `projects/03-script-review-pipeline/set/defects/02-Clear-OldLogFiles__02-G.ps1` | `cf05670c77da6484` |  |
| `projects/03-script-review-pipeline/set/defects/02-Clear-OldLogFiles__02-L.ps1` | `1db05c05b81c4d80` |  |
| `projects/03-script-review-pipeline/set/defects/03-Get-LowDiskReport__03-G1.ps1` | `89ae6a2e7e84915e` |  |
| `projects/03-script-review-pipeline/set/defects/03-Get-LowDiskReport__03-G2.ps1` | `8ca45df74a738d76` |  |
| `projects/03-script-review-pipeline/set/defects/03-Get-LowDiskReport__03-G3.ps1` | `77338b2ee5651d74` |  |
| `projects/03-script-review-pipeline/set/defects/04-Test-TimeOffset__04-B.ps1` | `9f9dd0740a32c7b7` |  |
| `projects/03-script-review-pipeline/set/defects/04-Test-TimeOffset__04-G.ps1` | `23a9bcb9a62a7b70` |  |
| `projects/03-script-review-pipeline/set/defects/04-Test-TimeOffset__04-L.ps1` | `46123de3d6e9a926` |  |
| `projects/03-script-review-pipeline/set/defects/05-Set-ServiceStartupType__05-B.ps1` | `c79510b479a41e7f` |  |
| `projects/03-script-review-pipeline/set/defects/05-Set-ServiceStartupType__05-G.ps1` | `3557e6a2ef3aa37f` |  |
| `projects/03-script-review-pipeline/set/defects/05-Set-ServiceStartupType__05-L.ps1` | `ed8bf625fe0bc675` |  |
| `projects/03-script-review-pipeline/set/defects/06-Export-LocalAdminMembers__06-G1.ps1` | `6f5a3a04d99bb21e` |  |
| `projects/03-script-review-pipeline/set/defects/06-Export-LocalAdminMembers__06-G2.ps1` | `577f074f4aeb8a61` |  |
| `projects/03-script-review-pipeline/set/defects/06-Export-LocalAdminMembers__06-G3.ps1` | `0cab6e1cc8747857` |  |
| `projects/03-script-review-pipeline/set/defects/07-Move-OldFilesToArchive__07-B.ps1` | `82d9afcfb8640e41` |  |
| `projects/03-script-review-pipeline/set/defects/07-Move-OldFilesToArchive__07-G.ps1` | `8550e72bc838f932` |  |
| `projects/03-script-review-pipeline/set/defects/07-Move-OldFilesToArchive__07-L.ps1` | `7e0b708a2d85ad2f` |  |
| `projects/03-script-review-pipeline/set/defects/08-Test-WinRMReadiness__08-B.ps1` | `551ccaa8560febfc` |  |
| `projects/03-script-review-pipeline/set/defects/08-Test-WinRMReadiness__08-G.ps1` | `c690ec3989abe7cd` |  |
| `projects/03-script-review-pipeline/set/defects/08-Test-WinRMReadiness__08-L.ps1` | `21110557d54d4362` |  |
| `projects/03-script-review-pipeline/set/defects/09-Register-CleanupTask__09-B.ps1` | `74cc8d54aa68300c` |  |
| `projects/03-script-review-pipeline/set/defects/09-Register-CleanupTask__09-G.ps1` | `f21102df658b63cf` |  |
| `projects/03-script-review-pipeline/set/defects/09-Register-CleanupTask__09-L.ps1` | `6f336af6fb46965b` |  |
| `projects/03-script-review-pipeline/set/defects/10-Test-PendingReboot__10-G1.ps1` | `62d141623172b2d2` |  |
| `projects/03-script-review-pipeline/set/defects/10-Test-PendingReboot__10-G2.ps1` | `9691bd87212f6892` |  |
| `projects/03-script-review-pipeline/set/defects/10-Test-PendingReboot__10-G3.ps1` | `3b27d11662760d0d` |  |
| `projects/03-script-review-pipeline/set/defects/11-Remove-StaleProfiles__11-B.ps1` | `51a839c89a8af328` |  |
| `projects/03-script-review-pipeline/set/defects/11-Remove-StaleProfiles__11-G.ps1` | `37338bf16083606f` |  |
| `projects/03-script-review-pipeline/set/defects/11-Remove-StaleProfiles__11-L.ps1` | `2290d9a6f06f9ee5` |  |
| `projects/03-script-review-pipeline/set/defects/12-Backup-ConfigFiles__12-B.ps1` | `c3b98464c03c4ec8` |  |
| `projects/03-script-review-pipeline/set/defects/12-Backup-ConfigFiles__12-G.ps1` | `846c252f6803521b` |  |
| `projects/03-script-review-pipeline/set/defects/12-Backup-ConfigFiles__12-L.ps1` | `f10ed77cda00f001` |  |
| `projects/03-script-review-pipeline/set/defects_spec.py` | `c601386e5b7c5762` |  |
| `projects/03-script-review-pipeline/set/reference/01-Start-StoppedAutoServices.ps1` | `e37c9977da947043` |  |
| `projects/03-script-review-pipeline/set/reference/02-Clear-OldLogFiles.ps1` | `a19daf62f59dc475` |  |
| `projects/03-script-review-pipeline/set/reference/03-Get-LowDiskReport.ps1` | `f6cd746812cc8f6a` |  |
| `projects/03-script-review-pipeline/set/reference/04-Test-TimeOffset.ps1` | `be150cd264089952` |  |
| `projects/03-script-review-pipeline/set/reference/05-Set-ServiceStartupType.ps1` | `7cdbc0eced3c8a69` |  |
| `projects/03-script-review-pipeline/set/reference/06-Export-LocalAdminMembers.ps1` | `b8784d25419c007d` |  |
| `projects/03-script-review-pipeline/set/reference/07-Move-OldFilesToArchive.ps1` | `025e35bb8ce87699` |  |
| `projects/03-script-review-pipeline/set/reference/08-Test-WinRMReadiness.ps1` | `ba27bc7594ca983b` |  |
| `projects/03-script-review-pipeline/set/reference/09-Register-CleanupTask.ps1` | `ef10c25146fd6e4a` |  |
| `projects/03-script-review-pipeline/set/reference/10-Test-PendingReboot.ps1` | `dc3fa4afa1adcf21` |  |
| `projects/03-script-review-pipeline/set/reference/11-Remove-StaleProfiles.ps1` | `8721892685b310fd` |  |
| `projects/03-script-review-pipeline/set/reference/12-Backup-ConfigFiles.ps1` | `033811ee8cb14d0b` |  |
| `projects/03-script-review-pipeline/set/split.json` | `d520497238a8d01b` |  |
| `projects/04-agent-evaluation-harness/README.md` | `518b249a3ebccd57` |  |
| `projects/05-remediation-agent/README.md` | `5293dbe0f8c8f4d2` |  |
| `projects/06-command-center/README.md` | `ea388f469fbb472e` | 3 |
| `projects/06-command-center/evidence/build-step-05-promote-on-srv01.md` | `60aff9a23395b148` |  |
| `projects/06-command-center/evidence/recording-run/20261024T052053Z-cc-promote-4df59f.json` | `ccbb5f293e3ef9c3` | 2 |
| `projects/06-command-center/evidence/recording-run/20261024T052055Z-promote-promote-bad-syslog-1a274d.json` | `592b17b75f8c0b6b` |  |
| `projects/06-command-center/evidence/step-05-runs/20261023T034703Z-cc-promote-dc1b44.json` | `858a28eac97876c3` | 2 |
| `projects/06-command-center/evidence/step-05-runs/20261023T034704Z-promote-promote-clean-applog-5b4808.json` | `9d141fa8606a4dbf` |  |
| `projects/06-command-center/evidence/step-05-runs/20261023T035641Z-cc-promote-7d08b9.json` | `9fb08c53dee99d17` | 2 |
| `projects/06-command-center/evidence/step-05-runs/20261023T035642Z-promote-promote-clean-applog-c72e81.json` | `8cc2ded339db1511` |  |
| `projects/06-command-center/evidence/step-05-runs/20261023T041815Z-cc-promote-225279.json` | `6a44056df70cc059` | 2 |
| `projects/06-command-center/evidence/step-05-runs/20261023T041817Z-promote-promote-clean-applog-1edc88.json` | `51770e8940095a7a` |  |
| `projects/06-command-center/evidence/step-05-runs/20261023T042311Z-cc-promote-5d4b76.json` | `ca21a70ef39f0b85` | 2 |
| `projects/06-command-center/evidence/step-05-runs/20261023T042313Z-promote-promote-clean-applog-4ef69b.json` | `ba4d53555781af67` | 2 |
| `projects/06-command-center/evidence/step-05-runs/20261023T043901Z-cc-promote-497122.json` | `87d45e1293948a50` | 2 |
| `projects/06-command-center/evidence/step-05-runs/20261023T043903Z-promote-promote-bad-syslog-dcee57.json` | `532d6b3202753fdd` | 2 |
| `projects/06-command-center/evidence/step-05-runs/20261023T161223Z-cc-promote-66b13c.json` | `025d6c1455f2defb` | 2 |
| `projects/06-command-center/evidence/step-05-runs/20261023T161225Z-promote-promote-bad-syslog-728c90.json` | `e6e30ff491a14c9a` |  |
| `projects/06-command-center/evidence/step-05-runs/20261023T185427Z-validate-srv01.json` | `c334b452d923180f` |  |
| `projects/06-command-center/evidence/step-05-runs/20261023T190855Z-p3-escalator-development.json` | `485b48059795c9f2` |  |
| `projects/06-command-center/evidence/step-05-runs/false-promotion/NOTE.md` | `85bdf08d3232a92d` |  |
| `projects/06-command-center/evidence/step-05-runs/false-promotion/promote-bad-syslog-2bb632b1/evidence.json` | `8406705cf81411b1` |  |
| `projects/06-command-center/evidence/step-05-runs/false-promotion/promote-bad-syslog-2bb632b1/promote-bad-syslog.ps1` | `2bb632b1fe737a36` |  |
| `projects/06-command-center/recording/p06-promote-recording.mp4` | `c4211c6dcbba570f` |  |
| `projects/06-command-center/samples/clean-spooler-disabled.ps1` | `7e3e56efec63c426` |  |
| `projects/06-command-center/samples/promote-bad-syslog.ps1` | `2bb632b1fe737a36` |  |
| `projects/06-command-center/samples/promote-clean-applog.ps1` | `af181269c3090c1b` |  |
| `projects/06-command-center/samples/refuse-cmdlet-no-whatif.ps1` | `7400426b4d3c20c3` |  |
| `projects/06-command-center/samples/refuse-native-auditpol.ps1` | `48edd47ae9d27824` |  |
| `projects/06-command-center/samples/refuse-no-shouldprocess.ps1` | `9079fee840239c6d` |  |
| `projects/06-command-center/samples/refuse-remote-target.ps1` | `53099c08f9d83816` |  |
| `promoted/promote-clean-applog-af181269/evidence.json` | `6120cbbd8cfcd235` |  |
| `promoted/promote-clean-applog-af181269/promote-clean-applog.ps1` | `af181269c3090c1b` |  |
| `requirements.txt` | `7e6c7dd49747f918` |  |
| `sops/bench-startup.md` | `e489e927c271b235` | 5 |
| `sops/identify-the-shell.md` | `af14d07a45ae419d` | 3 |
| `sops/jea-read-only-endpoint.md` | `1290b42b72675d46` |  |
| `sops/lab-session-closeout.md` | `9670c82240872089` | 3 |
| `src/agent.py` | `6aec98ec407fccdb` |  |
| `src/answer.py` | `e907730653e6faa6` |  |
| `src/approval.py` | `d202fe10da651ac6` |  |
| `src/audit.py` | `abe6177a948bf109` |  |
| `src/bench_check.py` | `adb5ce34b73cd029` |  |
| `src/command_center.py` | `a6911147805df30a` |  |
| `src/drift.py` | `2d02c2c0a730d83c` |  |
| `src/entry.py` | `be9951fa973982b8` |  |
| `src/eval_harness.py` | `44ea6981d28faded` |  |
| `src/faithfulness.py` | `7f229043fda94f9b` |  |
| `src/fake_bench.py` | `72c0059d5cb7ced2` |  |
| `src/guard.py` | `6fb585ae99f4b2a6` |  |
| `src/host_client.py` | `a0d5734a885f4823` |  |
| `src/injection.py` | `1070bdcfeea23c5e` |  |
| `src/jea/AgentReader-DC.pssc` | `ba50418567c12313` |  |
| `src/jea/AgentReader.pssc` | `041382dee5461ef7` |  |
| `src/jea/AgentReader/AgentReader.psd1` | `02069ccec81cf02d` |  |
| `src/jea/AgentReader/AgentReader.psm1` | `330faff714568174` |  |
| `src/jea/AgentReader/RoleCapabilities/AgentReader.psrc` | `f50c51a27580ace3` |  |
| `src/jea/AgentReader/RoleCapabilities/AgentReaderVerify.psrc` | `42c9419445229043` |  |
| `src/jea/AgentRemediator.pssc` | `5138f8c84e369fb3` |  |
| `src/jea/AgentRemediator/AgentRemediator.psd1` | `86006fc9ea80b4fc` |  |
| `src/jea/AgentRemediator/AgentRemediator.psm1` | `3a99f7d6267dfa4e` |  |
| `src/jea/AgentRemediator/RoleCapabilities/AgentRemediator.psrc` | `742e53d17b082ac7` |  |
| `src/jea/Test-AgentReaderParser.ps1` | `0f669ee00abd36d8` |  |
| `src/jea/Test-AgentReaderVerify.ps1` | `a4b05ea9f7c263e0` |  |
| `src/jea/Test-AgentRemediator.ps1` | `4e396800ab75a1fc` |  |
| `src/manifest.py` | `49f7d9e438038b5e` |  |
| `src/memory.py` | `bdf1eaad16fbe895` |  |
| `src/model_client.py` | `c7b3f33b3c8d3f17` |  |
| `src/promote.py` | `f4b63176a3a64ffb` |  |
| `src/proposer.py` | `a0c4368b862379ea` |  |
| `src/remediate.py` | `9eb2917d5ba3875b` |  |
| `src/remediation_agent.py` | `721e953805858a8c` |  |
| `src/remediation_gate.py` | `48dbae177b55abce` |  |
| `src/report.py` | `48d6cc429b230ba0` |  |
| `src/retrieval.py` | `20e7cbcca2214510` |  |
| `src/review.py` | `509252b0882d1c07` |  |
| `src/review_loop.py` | `a491265f5fdc09bd` |  |
| `src/review_runner.py` | `4db3b99de0ab8c65` |  |
| `src/review_script.py` | `ca6e092f7cf1fbaa` |  |
| `src/run_record.py` | `d6b194ee59443ac2` |  |
| `src/sampling_probe.py` | `4d48ccf5fa3bf032` |  |
| `src/score.py` | `9c588908b7b0f9ed` |  |
| `src/scorers.py` | `434277be8f59323d` |  |
| `src/srv01_bench.py` | `6cbb26f1c1046e65` | 2 |
| `src/srv01_detect.py` | `e11d1e113a72eec3` |  |
| `src/structured.py` | `be8aa3af538ef237` |  |
| `src/suite.py` | `d42ade0079de5728` |  |
| `src/target.py` | `250aeac2b818960b` |  |
| `src/team.py` | `23ddbe80e3078478` |  |
| `src/tools.py` | `08dd5fdd0a6c4bcf` |  |
| `src/tracing.py` | `42c695a9412054dd` |  |
| `src/validate.py` | `757785592025a582` |  |
| `src/variance.py` | `da91406d7d10d793` |  |
| `tests/run_checks.py` | `bd857bb1b426cab8` | 1 |
| `tests/test_answer_render.py` | `7236e061351afecd` |  |
| `tests/test_answer_scorer.py` | `2a60387493b45d18` |  |
| `tests/test_answer_step.py` | `959f9e65031dcc5e` |  |
| `tests/test_audit_and_screen.py` | `2652d57721ffa81e` |  |
| `tests/test_bench_pin.py` | `b214632b26ca66a9` |  |
| `tests/test_calibration_text.py` | `7bec29dafa2c42bb` |  |
| `tests/test_command_center.py` | `2b90f9ee6a8ad2e4` |  |
| `tests/test_committed_artifacts.py` | `86e5a04074b0ffcd` |  |
| `tests/test_comparability.py` | `bde3d5f96a57472b` |  |
| `tests/test_derive.py` | `b468859a27ee1d2b` |  |
| `tests/test_drift.py` | `24287aa4762b1e9b` |  |
| `tests/test_entry_guard.py` | `563a880beaa1e5a3` |  |
| `tests/test_eval_harness.py` | `54e6866dcf922770` |  |
| `tests/test_faithfulness.py` | `031cbc45e4005d4c` |  |
| `tests/test_generation_cap.py` | `c177ffa2fd43aceb` |  |
| `tests/test_incomplete_record.py` | `3ba94407111346d0` |  |
| `tests/test_injection.py` | `d0f5f4cc9556ad02` |  |
| `tests/test_isolation.py` | `2408652c1456c835` |  |
| `tests/test_judge_error_gating.py` | `be64cc8b178d44d3` |  |
| `tests/test_manifest.py` | `9bcca065ace8e961` |  |
| `tests/test_measured_run.py` | `3a944098bc32e91e` |  |
| `tests/test_model_digest.py` | `46c2c4deaafe7e42` |  |
| `tests/test_negative_case.py` | `a82c6fa8974ca203` |  |
| `tests/test_promote.py` | `0298c671c80a6415` |  |
| `tests/test_proposer.py` | `c0d0718b5a99585b` |  |
| `tests/test_provenance.py` | `54b2577d108ef9aa` |  |
| `tests/test_rebaseline.py` | `d6690d1526a2f6ec` |  |
| `tests/test_remediate.py` | `a1bf7c3a0b6fb8cf` |  |
| `tests/test_remediation_agent.py` | `369d38b010e7cbc5` |  |
| `tests/test_remediation_live.py` | `24a879f5ee9a5da6` |  |
| `tests/test_remediation_scorer.py` | `bcdfd9c94a4a3d07` |  |
| `tests/test_report.py` | `17641f11f9eb3d3d` |  |
| `tests/test_retrieval_seam.py` | `fb7007c3a59fc8f5` |  |
| `tests/test_review.py` | `c3d6767e48437619` |  |
| `tests/test_review_loop.py` | `e20c1fcf5f41520c` |  |
| `tests/test_review_runner.py` | `ad63e5063ee8d4c3` |  |
| `tests/test_review_scorer.py` | `2c3e89b7a3487190` |  |
| `tests/test_review_script.py` | `f14df357a93adfbe` |  |
| `tests/test_run_checks.py` | `16ffca49a19d1e87` |  |
| `tests/test_run_record.py` | `b908750cfc929b47` |  |
| `tests/test_sampling_condition.py` | `1e23bbe74c440a19` |  |
| `tests/test_sampling_probe.py` | `27ed292ff6b5ea6c` |  |
| `tests/test_sampling_seams.py` | `6b455ce4a77269a9` |  |
| `tests/test_scorer_table.py` | `f6d7813c67f9ea0d` |  |
| `tests/test_splitter.py` | `387c4fdfb1872666` |  |
| `tests/test_srv01_bench.py` | `1f1938a19e19603c` |  |
| `tests/test_srv_reader.py` | `ed7a7bf71be8311a` |  |
| `tests/test_srv_remediator.py` | `504fb472f4d62916` |  |
| `tests/test_stall.py` | `57a5ccc7369ec071` |  |
| `tests/test_stall_turn.py` | `3d40236f36b88900` |  |
| `tests/test_stance_order.py` | `18bb8ee908e9a464` |  |
| `tests/test_suite_header.py` | `e2a11cdabff043b7` |  |
| `tests/test_target.py` | `98b03eb44e129993` |  |
| `tests/test_threshold_band.py` | `259e8b9d4973e491` |  |
| `tests/test_validate.py` | `d9e34f93b22b34d9` |  |
| `tests/test_vocabulary.py` | `72989ab49a245865` |  |
| `tools/p06_bench_check.py` | `3fb4e4b721ce3ec2` |  |
| `tools/upgrade_check.py` | `319004ed93f39c94` |  |

Allow-listed scrub matches (reviewed, kept):

- src/jea/AgentReader/AgentReader.psm1:359 (credential assignment)
- tests/test_audit_and_screen.py:134 (private key)
- tests/test_provenance.py:44 (e-mail address)
- tests/test_validate.py:37 (credential assignment)
- projects/06-command-center/README.md:128 (credential assignment)
- projects/06-command-center/recording/p06-promote-recording.mp4 (binary, pinned by sha256)
