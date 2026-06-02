# Test Layers thoughts
Keep the up_to model, its great!

## What to keep?
- App2 Dashboard and Quicksight Parity at Release
- Database layer is truely pluggable
- Studio is duck/ora/pg
- Dashboards are qs|app2 x ora/pg

## Pain
- AWS Costs
- AWS Database start up / shutdown time
- CI Speed
- CI Stomping on each other

## Help
- We have access to nice beefy box to run stuff on!
- There are Oracle 19c docker images! (See https://hub.docker.com/r/doctorkirk/oracle-19c or even Oracle's official images)

## Open Questions: 
- How much to keep in AWS?
  - Should we keep an Oracle DB?
- When should it be run?
- How can we better define what a test needs? Annotations? (Will this require a Python upgrade?)
  - Right now I think we have huge hand maintained arrays instead of annotations on each test fixture declaring dialects 
- Idea: Could the docker dbs be exposed to AWS from the self hosted runner? 
  - It would nuke a major cost leg of AWS and we control the scaling way more.
  - Have a public ipv4 and can port forward
  - AWS published their outbound ip range here: 52.23.63.224/27 (I could lock down the port forwarding)
  - Couldn't do it before because of github's runner limitations

## Layer / Cell Model (it is great!)
1. unit (no L2 impact)
2. db (per dialect)
  - 100% local/docker based
  - (duck/pg/ora) 
3. e2e
  - what is REALLY inside this? is it the start of l2 testing?
  - hoping this is still 100% local/docker based
  - (duck/pg/ora)
4. app2 browser
  - 100% local/docker based (duck/pg/ora)
5. qs api
  - aws oracle+postges
6. qs browser
  - aws postgres only

## What runs when in CI
- On push to main
  - up to app2 browser
- On release
  - up to qs browser

## Decisions
- sqlite is gone, too slow
