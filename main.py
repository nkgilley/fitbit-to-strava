import os
import json
import time
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fitbit_client import FitbitClient
from strava_client import StravaClient
from merger import create_tcx, parse_tcx, parse_fit

SYNC_LOG = "sync_log.json"

def load_sync_log():
    if os.path.exists(SYNC_LOG):
        with open(SYNC_LOG, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                pass
    return {"pending_cleanup": [], "completed": []}

def save_sync_log(log):
    with open(SYNC_LOG, "w") as f:
        json.dump(log, f, indent=4)

def cleanup_activities(strava):
    log = load_sync_log()
    pending = log.get("pending_cleanup", [])
    
    if not pending:
        print("No activities pending cleanup in sync_log.json.")
        return

    print(f"Found {len(pending)} activities pending cleanup.")
    remaining = []
    for item in pending:
        old_id = item["old_id"]
        name = item.get("name", "Unknown")
        date = item.get("date", "Unknown")
        
        confirm = input(f"Delete original activity {old_id} ('{name}' on {date})? (y/N/all): ")
        if confirm.lower() == 'all':
            to_delete = [i for i in pending if i not in remaining]
            for d_item in to_delete:
                try:
                    print(f"  Deleting {d_item['old_id']}...")
                    strava.delete_activity(d_item['old_id'])
                    time.sleep(1)
                except Exception as e:
                    print(f"  Failed to delete {d_item['old_id']}: {e}")
                    remaining.append(d_item)
            break
        elif confirm.lower() == 'y':
            try:
                print(f"  Deleting {old_id}...")
                strava.delete_activity(old_id)
                print("  Deleted successfully.")
                time.sleep(1)
            except Exception as e:
                print(f"  Failed to delete {old_id}: {e}")
                remaining.append(item)
        else:
            print(f"  Skipping {old_id}.")
            remaining.append(item)
            
    log["pending_cleanup"] = remaining
    save_sync_log(log)
    print("\nCleanup complete. sync_log.json updated.")

def parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%fZ")

def main():
    parser = argparse.ArgumentParser(description="Sync Fitbit HR data to Strava activities.")
    parser.add_argument("--dry-run", action="store_true", help="Don't upload to Strava or modify anything.")
    parser.add_argument("--cleanup", action="store_true", help="Delete old activities that have already been synced.")
    parser.add_argument("--limit", type=int, default=0, help="Limit the number of activities to process.")
    parser.add_argument("--id", type=int, help="Process a specific Strava activity ID.")
    parser.add_argument("--file", type=str, help="Process a local FIT/TCX file instead of fetching from Strava.")
    parser.add_argument("--bypass-duplicate", action="store_true", help="Shift start time by 1 second to bypass Strava duplicate detection.")
    parser.add_argument("--force-elevation", action="store_true", help="Force Strava to recalculate elevation by omitting device info.")
    args = parser.parse_args()

    load_dotenv()
    
    try:
        strava = StravaClient()
    except Exception as e:
        print(f"Error initializing Strava client: {e}")
        return

    if args.cleanup:
        cleanup_activities(strava)
        return

    try:
        fitbit = FitbitClient()
    except Exception as e:
        print(f"Error initializing Fitbit client: {e}")
        return

    log = load_sync_log()
    completed_ids = {item["old_id"] for item in log.get("completed", [])}
    
    missing_hr_data = [] # List of (activity_dict, streams_dict)

    if args.file:
        if args.file.lower().endswith(".fit"):
            print(f"Loading local FIT file: {args.file}")
            activity, streams = parse_fit(args.file)
        else:
            print(f"Loading local TCX file: {args.file}")
            activity, streams = parse_tcx(args.file)
        
        act_id = os.path.basename(args.file).split(".")[0]
        activity["id"] = act_id
        missing_hr_data.append((activity, streams))
    else:
        print("Fetching recent Strava activities...")
        activities = strava.get_activities(per_page=50)
        
        target_activities = []
        if args.id:
            target_activities = [a for a in activities if a["id"] == args.id]
            if not target_activities:
                print(f"Activity {args.id} not found in the most recent 50 activities.")
                return
        else:
            target_activities = [a for a in activities if not a.get("has_heartrate") and a["id"] not in completed_ids]

        if not target_activities:
            print("No activities found to process.")
            return
            
        if args.limit > 0:
            target_activities = target_activities[:args.limit]
            print(f"Limited to first {args.limit} activities.")
        
        for activity in target_activities:
            print(f"  Fetching streams for {activity['id']}...")
            streams = strava.get_activity_streams(activity["id"])
            missing_hr_data.append((activity, streams))

    print(f"Processing {len(missing_hr_data)} activities.")
    
    os.makedirs("backups", exist_ok=True)
    os.makedirs("outputs", exist_ok=True)
    
    for activity, streams in missing_hr_data:
        act_id = activity["id"]
        act_name = activity.get("name", "Unknown Activity")
        start_date_local = activity.get("start_date_local")
        print(f"\n--- Processing Activity {act_id}: {act_name} ---")
        
        if not args.file:
            time.sleep(1)
        
        start_dt = parse_date(start_date_local)
        
        if streams.get("time") and streams["time"].get("data"):
            duration = streams["time"]["data"][-1]
        else:
            duration = activity.get("elapsed_time", 3600)
            
        end_dt = start_dt + timedelta(seconds=duration)
        date_str = start_dt.strftime("%Y-%m-%d")
        start_time_str = start_dt.strftime("%H:%M")
        end_time_str = (end_dt + timedelta(minutes=5)).strftime("%H:%M")
        
        try:
            print(f"  Fetching Fitbit HR data...")
            hr_data = fitbit.get_hr_data(date_str, start_time_str, end_time_str)
            
            if not hr_data:
                print("  No Fitbit HR data found for this time period.")
                continue
                
            print(f"  Found {len(hr_data)} HR data points.")
            
            if not args.file:
                backup_file = f"backups/{act_id}_original.tcx"
                print(f"  Saving original backup to {backup_file}...")
                create_tcx(activity, streams, {}, backup_file, include_creator=(not args.force_elevation))
            
            if args.bypass_duplicate:
                start_dt_utc = parse_date(activity.get('start_date'))
                start_dt_local = parse_date(activity.get('start_date_local'))
                activity['start_date'] = (start_dt_utc + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                activity['start_date_local'] = (start_dt_local + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                print("  Applied 1-second shift to bypass duplicate detection.")

            output_file = f"outputs/{act_id}_with_hr.tcx"
            print(f"  Generating merged TCX file...")
            create_tcx(activity, streams, hr_data, output_file, include_creator=(not args.force_elevation))
            
            # Debug Elevation Gain
            a_data = [a for a in streams.get("altitude", {}).get("data", []) if a is not None]
            if a_data:
                gain = 0
                for i in range(1, len(a_data)):
                    diff = a_data[i] - a_data[i-1]
                    if diff > 0: gain += diff
                print(f"  [Debug] Raw elevation gain in file: {gain * 3.28084:.0f} ft")

            if args.dry_run:
                print("  Dry-run: skipping upload.")
            else:
                print("  Attempting upload to Strava...")
                desc = activity.get("description", "") or ""
                if desc: desc += "\n\n"
                desc += "(Heart rate data added via Fitbit sync)"
                
                try:
                    upload_resp = strava.upload_activity(
                        file_path=output_file,
                        data_type="tcx",
                        name=act_name,
                        description=desc,
                        trainer=activity.get("trainer", False),
                        commute=activity.get("commute", False),
                        gear_id=activity.get("gear_id") # Preserve original bike
                    )
                    new_id = upload_resp.get("activity_id")
                    print(f"  Upload successful! New Activity: https://www.strava.com/activities/{new_id}")
                    
                    log["completed"] = log.get("completed", [])
                    log["completed"].append({"old_id": act_id, "new_id": new_id, "date": start_date_local})
                    save_sync_log(log)
                    
                except Exception as upload_err:
                    if "duplicate" in str(upload_err).lower():
                        print(f"  [!] Strava blocked upload as a duplicate.")
                        print(f"  To fix this, you must EITHER:")
                        print(f"    1. Manually delete the original activity on Strava.")
                        print(f"    2. Run again with the --bypass-duplicate flag.")
                    else:
                        raise upload_err
        
        except Exception as e:
            print(f"  Error processing activity {act_id}: {e}")
            continue

if __name__ == "__main__":
    main()
