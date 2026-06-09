# 1. Wipe both broken venvs
sudo -u recon-demo rm -rf /Users/recon-demo/venv /Users/recon-demo/venv-313-old

# 2. Create fresh AT THE FINAL PATH (no rename, no broken shebangs)
sudo -u recon-demo /opt/homebrew/bin/python3.14 -m venv --upgrade-deps /Users/recon-demo/venv

# 3. Install with the actual current extra ([prod] not [deploy,demo,audit,serve])
sudo -u recon-demo /Users/recon-demo/venv/bin/pip install "recon-gen[prod]"

# 4. Verify — should report 13.10.1, NO "does not provide the extra" warnings
sudo -u recon-demo /Users/recon-demo/venv/bin/recon-gen --version

# 5. Drop the v13.10.1 refresh-demos.sh (fixes [prod] extras for future nightly runs).
#    git clone keeps URL on one line; tag checkout pins us to the exact release artifact.
TMP=$(mktemp -d) && cd "$TMP"
git clone --depth=1 -b v13.10.1 https://github.com/chotchki/recon-gen.git
sudo install -o recon-demo -g staff -m 0500 \
  recon-gen/deploy/launchd/refresh-demos.sh \
  /Users/recon-demo/bin/refresh-demos.sh
cd ~ && rm -rf "$TMP"

# 5b. Verify the swap landed (first three lines of the new script).
sudo head -3 /Users/recon-demo/bin/refresh-demos.sh

# 6. Now run refresh — pip will say already-satisfied, then build both DuckDBs + audit-verify each.
sudo -u recon-demo /Users/recon-demo/bin/refresh-demos.sh

# 7. After refresh succeeds, bootout + bootstrap each launchd job so it reloads new wrapper / sandbox / cfg.
sudo launchctl bootout system /Library/LaunchDaemons/io.hotchkiss.recon-demo.sasquatch.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/io.hotchkiss.recon-demo.sasquatch.plist
sudo launchctl bootout system /Library/LaunchDaemons/io.hotchkiss.recon-demo.spec.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/io.hotchkiss.recon-demo.spec.plist

# 8. Verify HTTP 200 + banner copy renders.
sleep 10
curl -sI --max-time 30 -L https://recon-gen-sasquatch.hotchkiss.io/dashboards/l1_dashboard | head -3
curl -sI --max-time 30 -L https://recon-gen-spec.hotchkiss.io/dashboards/l1_dashboard | head -3
curl -s --max-time 30 -L https://recon-gen-sasquatch.hotchkiss.io/dashboards/l1_dashboard | grep -c "Demo only"
