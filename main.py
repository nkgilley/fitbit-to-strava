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
    return {"pending_cleanup": [], "completed": [], "skipped": []}

def save_sync_log(log):
    with open(SYNC_LOG, "w") as f:
        json.dump(log, f, indent=4)

def cleanup_activities(strava):
    log = load_sync_log()
    pending = log.get("pending_cleanup", [])
    
    if not pending and not log.get("completed"):
        print("No activities in sync_log.json.")
        return

    print(f"Verifying {len(pending)} pending activities on Strava...")
    still_pending = []
    completed = log.get("completed", [])
    
    import requests
    for item in pending:
        old_id = item["old_id"]
        try:
            strava.get_activity_streams(old_id)
            still_pending.append(item)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f"  Confirmed: Activity {old_id} was manually deleted. Moving to completed.")
                completed.append(item)
            else:
                still_pending.append(item)
        except Exception:
            still_pending.append(item)

    print("Backfilling missing stats from Strava...")
    all_items = still_pending + completed
    for item in all_items:
        if 'distance_mi' not in item or 'elevation_gain_ft' not in item or item.get('name') == 'N/A' or not item.get('name'):
            try:
                new_id = item.get("new_id")
                if not new_id: continue
                
                print(f"  Fetching stats for new activity {new_id}...")
                url = f"https://www.strava.com/api/v3/activities/{new_id}"
                act_data = strava._request("GET", url)
                
                item["name"] = act_data.get("name", item.get("name"))
                item["distance_mi"] = round(act_data.get("distance", 0) / 1609.34, 2)
                item["duration_min"] = round(act_data.get("moving_time", 0) / 60.0, 1)
                
                if act_data.get("total_elevation_gain"):
                    item["elevation_gain_ft"] = int(act_data.get("total_elevation_gain") * 3.28084)
                else:
                    item["elevation_gain_ft"] = 0
                
                time.sleep(0.5)
            except Exception as e:
                print(f"  Failed to backfill {item.get('new_id')}: {e}")

    log["pending_cleanup"] = still_pending
    log["completed"] = completed
    save_sync_log(log)
    
    if len(pending) != len(still_pending):
        print(f"\nDashboard updated: {len(pending) - len(still_pending)} activities moved to 'Completed'.")
    else:
        print("\nNo log movements. (Manually delete originals on Strava to move them to 'Completed').")
    
    print(f"Remaining pending cleanup: {len(still_pending)}")

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
    parser.add_argument("--pages", type=int, default=1, help="Number of pages (50 per page) to fetch from Strava history.")
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
    pending_ids = {item["old_id"] for item in log.get("pending_cleanup", [])}
    
    raw_skipped = log.get("skipped", [])
    skipped_ids = set()
    for s in raw_skipped:
        if isinstance(s, dict): skipped_ids.add(s["id"])
        else: skipped_ids.add(s)
    
    missing_hr_data = []

    if args.file:
        if args.file.lower().endswith(".fit"):
            print(f"Loading local FIT file: {args.file}")
            activity, streams = parse_fit(args.file)
        else:
            print(f"Loading local TCX file: {args.file}")
            activity, streams = parse_tcx(args.file)
        
        has_hr = False
        if activity.get("has_heartrate"):
            has_hr = True
        elif streams.get("heartrate") and any(h is not None for h in streams["heartrate"].get("data", [])):
            has_hr = True
            
        if has_hr:
            print(f"  [Skip] Local file already contains heart rate data.")
            return

        act_id = os.path.basename(args.file).split(".")[0]
        activity["id"] = act_id
        missing_hr_data.append((activity, streams))
    else:
        print(f"Fetching Strava activities ({args.pages} pages)...")
        activities = []
        for p in range(1, args.pages + 1):
            print(f"  Page {p}...")
            page_data = strava.get_activities(per_page=50, page=p)
            if not page_data: break
            activities.extend(page_data)
            time.sleep(0.5)
        
        target_activities = []
        if args.id:
            target_activities = [a for a in activities if a["id"] == args.id]
            if not target_activities:
                print(f"Activity {args.id} not found in fetched activities.")
                return
        else:
            for a in activities:
                if a.get("has_heartrate") or a["id"] in completed_ids or a["id"] in pending_ids or a["id"] in skipped_ids:
                    continue
                if a.get("total_photo_count", 0) > 0:
                    print(f"  [Skip] Activity {a['id']} has photos. Skipping.")
                    log["skipped"] = log.get("skipped", [])
                    log["skipped"].append({"id": a["id"], "name": a.get("name", "Unknown") + " (Has Photos)", "date": a.get("start_date_local", "N/A")})
                    save_sync_log(log)
                    continue
                target_activities.append(a)

        if not target_activities:
            print("No activities found to process.")
            return
            
        if args.limit > 0:
            target_activities = target_activities[:args.limit]
            print(f"Limited to first {args.limit} activities.")
        
        for activity in target_activities:
            try:
                print(f"  Fetching streams for {activity['id']}...")
                streams = strava.get_activity_streams(activity["id"])
                missing_hr_data.append((activity, streams))
            except Exception as e:
                if "404" in str(e):
                    print(f"  [Skip] Activity {activity['id']} not found. Adding to skipped list.")
                    log["skipped"] = log.get("skipped", [])
                    log["skipped"].append({"id": activity["id"], "name": activity.get("name", "Unknown"), "date": activity.get("start_date_local", "N/A")})
                    save_sync_log(log)
                else:
                    print(f"  [Skip] Could not fetch streams for {activity['id']}: {e}")
                continue

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
        act_duration_sec = activity.get("elapsed_time") or activity.get("moving_time")
        if streams.get("time") and streams["time"].get("data"):
            act_duration_sec = streams["time"]["data"][-1]
        
        if not act_duration_sec:
            act_duration_sec = 3600
            
        end_dt = start_dt + timedelta(seconds=act_duration_sec)
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
                # Use a 30 second shift to be more distinctive for Strava
                activity['start_date'] = (start_dt_utc + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
                activity['start_date_local'] = (start_dt_local + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
                print("  Applied 30-second shift to bypass duplicate detection.")

            output_file = f"outputs/{act_id}_with_hr.tcx"
            print(f"  Generating merged TCX file...")
            create_tcx(activity, streams, hr_data, output_file, include_creator=(not args.force_elevation))
            
            a_data = [a for a in streams.get("altitude", {}).get("data", []) if a is not None]
            total_gain_ft = 0
            if a_data:
                gain_m = 0
                for i in range(1, len(a_data)):
                    diff = a_data[i] - a_data[i-1]
                    if diff > 0: gain_m += diff
                total_gain_ft = int(gain_m * 3.28084)
            dist_km = streams.get("distance", {}).get("data", [-1])[-1] / 1000.0 if streams.get("distance") else 0
            dist_mi = dist_km * 0.621371

            if args.dry_run:
                print("  Dry-run: skipping upload.")
            else:
                print("  Attempting upload to Strava...")
                desc = activity.get("description", "") or ""
                if desc: desc += "\n\n"
                desc += "(Heart rate data added via Fitbit sync)"
                
                try:
                    act_type = activity.get("sport_type") or activity.get("type")
                    if act_type: act_type = act_type.replace(" ", "")
                    
                    upload_resp = strava.upload_activity(
                        file_path=output_file,
                        data_type="tcx",
                        name=act_name,
                        description=desc,
                        trainer=activity.get("trainer", False),
                        commute=activity.get("commute", False),
                        gear_id=activity.get("gear_id"),
                        activity_type=act_type
                    )
                    new_id = upload_resp.get("activity_id")
                    print(f"  Upload successful! New Activity: https://www.strava.com/activities/{new_id}")
                    
                    if act_type:
                        strava.update_activity(new_id, sport_type=act_type)

                    log["pending_cleanup"] = log.get("pending_cleanup", [])
                    log["pending_cleanup"].append({
                        "old_id": act_id, 
                        "new_id": new_id, 
                        "name": act_name,
                        "date": start_date_local,
                        "distance_mi": round(dist_mi, 2),
                        "duration_min": round(act_duration_sec / 60.0, 1),
                        "elevation_gain_ft": total_gain_ft
                    })
                    save_sync_log(log)
                    
                except Exception as upload_err:
                    if "duplicate" in str(upload_err).lower():
                        print(f"  [!] Strava blocked upload as a duplicate.")
                    else:
                        raise upload_err
        
        except Exception as e:
            print(f"  Error processing activity {act_id}: {e}")
            continue

if __name__ == "__main__":
    main()
