import os
import json
import time
import argparse
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fitbit_client import FitbitClient
from strava_client import StravaClient
from merger import create_tcx, parse_tcx, parse_fit
from database import SessionLocal, SyncedActivity, SkippedActivity, ScanResult, FixableActivity, init_db

# Helper to always flush print
def log(msg):
    print(msg, flush=True)

def decrement_scan_count(db, was_fixable=True, act_id=None):
    scan_record = db.query(ScanResult).filter(ScanResult.id == 1).first()
    if scan_record:
        if scan_record.count > 0:
            scan_record.count -= 1
        if was_fixable and scan_record.fixable_count > 0:
            scan_record.fixable_count -= 1
        db.commit()
    if act_id:
        db.query(FixableActivity).filter(FixableActivity.id == str(act_id)).delete()
        db.commit()

def cleanup_activities(strava):
    db = SessionLocal()
    try:
        pending = db.query(SyncedActivity).filter(SyncedActivity.status == "pending_cleanup").all()
        
        if not pending:
            completed = db.query(SyncedActivity).filter(SyncedActivity.status == "completed").all()
            if not completed:
                log("No activities in database.")
                return
            all_to_check = completed
        else:
            all_to_check = pending + db.query(SyncedActivity).filter(SyncedActivity.status == "completed").all()

        log(f"Verifying {len(pending)} pending activities on Strava...")
        
        import requests
        for item in pending:
            try:
                log(f"  Checking {item.old_id}...")
                strava.get_activity_streams(item.old_id)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    log(f"  Confirmed: Activity {item.old_id} was manually deleted. Moving to completed.")
                    item.status = "completed"
                    db.commit()
                    
                    # TRIGGER RE-ANALYSIS ON THE NEW ACTIVITY (Force PR scan)
                    try:
                        log(f"  Triggering PR/Segment scan for new activity {item.new_id}...")
                        url = f"https://www.strava.com/api/v3/activities/{item.new_id}"
                        act_data = strava._request("GET", url)
                        real_type = act_data.get("sport_type") or act_data.get("type", "Ride")
                        temp_type = "Hike" if "Hike" not in str(real_type) else "Walk"
                        
                        strava.update_activity(item.new_id, sport_type=temp_type)
                        time.sleep(1)
                        strava.update_activity(item.new_id, sport_type=real_type)
                        log(f"  PR scan triggered.")
                    except Exception as re_err:
                        log(f"  Warning: Could not trigger PR scan for {item.new_id}: {re_err}")

                    # If we verified a deletion, it's no longer 'missing'
                    decrement_scan_count(db, was_fixable=True, act_id=item.old_id)
            except Exception as e:
                log(f"  Unexpected error checking {item.old_id}: {e}")

        log("Backfilling missing stats from Strava...")
        for item in all_to_check:
            if item.distance_mi is None or item.elevation_gain_ft is None or not item.name or item.name == 'N/A' or not item.name:
                try:
                    if not item.new_id: continue
                    log(f"  Fetching stats for new activity {item.new_id}...")
                    url = f"https://www.strava.com/api/v3/activities/{item.new_id}"
                    act_data = strava._request("GET", url)
                    
                    item.name = act_data.get("name", item.name)
                    item.distance_mi = round(act_data.get("distance", 0) / 1609.34, 2)
                    item.duration_min = round(act_data.get("moving_time", 0) / 60.0, 1)
                    
                    if act_data.get("total_elevation_gain"):
                        item.elevation_gain_ft = int(act_data.get("total_elevation_gain") * 3.28084)
                    else:
                        item.elevation_gain_ft = 0
                    
                    db.commit()
                    time.sleep(0.5)
                except Exception as e:
                    log(f"  Failed to backfill {item.new_id}: {e}")
    finally:
        db.close()
        log("\nCleanup and backfill complete.")

def parse_date(date_str):
    if not date_str: return None
    try: return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError: return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%fZ")

def main():
    parser = argparse.ArgumentParser(description="Sync Fitbit HR data to Strava activities.")
    parser.add_argument("--dry-run", action="store_true", help="Don't upload to Strava or modify anything.")
    parser.add_argument("--cleanup", action="store_true", help="Verify manual deletions and backfill stats.")
    parser.add_argument("--limit", type=int, default=0, help="Limit the number of activities to process.")
    parser.add_argument("--pages", type=int, default=1, help="Number of pages to scan.")
    parser.add_argument("--id", type=int, help="Process a specific Strava activity ID.")
    parser.add_argument("--file", type=str, help="Process a local FIT/TCX file.")
    parser.add_argument("--bypass-duplicate", action="store_true", help="Shift start time by 30 seconds.")
    parser.add_argument("--force-elevation", action="store_true", help="Omit device info.")
    parser.add_argument("--only-fixable", action="store_true", help="Only process activities identified as fixable in the last scan.")
    args = parser.parse_args()

    load_dotenv()
    init_db()
    db = SessionLocal()
    
    try:
        strava = StravaClient()
    except Exception as e:
        log(f"Error initializing Strava client: {e}")
        db.close()
        return

    if args.cleanup:
        cleanup_activities(strava)
        db.close()
        return

    try:
        fitbit = FitbitClient()
    except Exception as e:
        log(f"Error initializing Fitbit client: {e}")
        db.close()
        return

    # Load exclusions from DB
    completed_ids = {a.old_id for a in db.query(SyncedActivity).filter(SyncedActivity.status == "completed").all()}
    pending_ids = {a.old_id for a in db.query(SyncedActivity).filter(SyncedActivity.status == "pending_cleanup").all()}
    
    raw_skipped = db.query(SkippedActivity).all()
    skipped_ids = {a.id for a in raw_skipped}
    
    missing_hr_data = []

    if args.file:
        if args.file.lower().endswith(".fit"):
            log(f"Loading local FIT file: {args.file}")
            activity, streams = parse_fit(args.file)
        else:
            log(f"Loading local TCX file: {args.file}")
            activity, streams = parse_tcx(args.file)
        
        has_hr = activity.get("has_heartrate") or (streams.get("heartrate") and any(h is not None for h in streams["heartrate"].get("data", [])))
        if has_hr:
            log(f"  [Skip] Local file already contains heart rate data.")
            db.close()
            return

        act_id = os.path.basename(args.file).split(".")[0]
        activity["id"] = act_id
        missing_hr_data.append((activity, streams))
    elif args.only_fixable:
        log("Fetching fixable activities from database cache...")
        fixable_recs = db.query(FixableActivity).all()
        if not fixable_recs:
            log("No fixable activities found in cache. Please run a scan first.")
            db.close()
            return

        processed_count = 0
        for rec in fixable_recs:
            try:
                log(f"  Loading fixable activity {rec.id} from cache...")
                # Use the cached data
                activity = rec.activity_data
                streams = rec.streams_data
                activity["id"] = rec.id
                activity["cached_hr_data"] = rec.hr_data
                
                missing_hr_data.append((activity, streams))
                processed_count += 1
                if args.limit > 0 and processed_count >= args.limit:
                    break
            except Exception as e:
                log(f"  [Skip] Error loading cached data for {rec.id}: {e}")
                continue
    else:
        # ONLY if no file and not only-fixable
        log(f"Fetching Strava activities ({args.pages} pages)...")
        activities = []
        for p in range(1, args.pages + 1):
            log(f"  Page {p}...")
            page_data = strava.get_activities(per_page=50, page=p)
            if not page_data: break
            activities.extend(page_data)
            time.sleep(0.5)
        
        target_activities = []
        if args.id:
            target_activities = [a for a in activities if str(a["id"]) == str(args.id)]
        else:
            for a in activities:
                a_id = str(a["id"])
                if a.get("has_heartrate") or a_id in completed_ids or a_id in pending_ids or a_id in skipped_ids:
                    continue
                if a.get("total_photo_count", 0) > 0:
                    log(f"  [Skip] Activity {a_id} has photos.")
                    skipped = SkippedActivity(id=a_id, name=a.get("name", "Unknown") + " (Has Photos)", date=a.get("start_date_local", "N/A"))
                    db.merge(skipped)
                    db.commit()
                    continue
                target_activities.append(a)

        if not target_activities:
            log("No activities found to process.")
            db.close()
            return
            
        if args.limit > 0:
            target_activities = target_activities[:args.limit]
        
        for activity in target_activities:
            try:
                log(f"  Fetching streams for {activity['id']}...")
                streams = strava.get_activity_streams(activity["id"])
                missing_hr_data.append((activity, streams))
            except Exception as e:
                if "404" in str(e):
                    log(f"  [Skip] Activity {activity['id']} not found.")
                    skipped = SkippedActivity(id=str(activity['id']), name=activity.get("name", "Unknown"), date=activity.get("start_date_local", "N/A"))
                    db.merge(skipped)
                    db.commit()
                else:
                    log(f"  [Skip] Could not fetch streams for {activity['id']}: {e}")
                continue

    log(f"Processing {len(missing_hr_data)} activities.")
    os.makedirs("backups", exist_ok=True)
    os.makedirs("outputs", exist_ok=True)
    
    for activity, streams in missing_hr_data:
        act_id = str(activity["id"])
        act_name = activity.get("name", "Unknown Activity")
        start_date_local = activity.get("start_date_local")
        log(f"\n--- Processing Activity {act_id}: {act_name} ---")
        
        if not args.file and not args.only_fixable:
            time.sleep(1)
        
        start_dt = parse_date(start_date_local)
        act_duration_sec = activity.get("elapsed_time") or activity.get("moving_time")
        if streams.get("time") and streams["time"].get("data"):
            act_duration_sec = streams["time"]["data"][-1]
        
        if not act_duration_sec: act_duration_sec = 3600
        end_dt = start_dt + timedelta(seconds=act_duration_sec)
        date_str, s_time, e_time = start_dt.strftime("%Y-%m-%d"), start_dt.strftime("%H:%M"), (end_dt + timedelta(minutes=5)).strftime("%H:%M")
        
        try:
            hr_data = activity.get("cached_hr_data")
            if hr_data:
                log(f"  Using cached heart rate data ({len(hr_data)} pts).")
            else:
                log(f"  Fetching Fitbit HR data...")
                hr_data = fitbit.get_hr_data(date_str, s_time, e_time)
            
            if not hr_data:
                log("  No Fitbit HR data found.")
                continue
                
            if not isinstance(hr_data, dict):
                log("  Wait, hr_data is not a dict. Skipping.")
                continue

            log(f"  Total HR points: {len(hr_data)}")
            if not args.file:
                backup_file = f"backups/{act_id}_original.tcx"
                create_tcx(activity, streams, {}, backup_file, include_creator=(not args.force_elevation))
            
            if args.bypass_duplicate:
                start_dt_utc = parse_date(activity.get('start_date'))
                start_dt_local_dt = parse_date(activity.get('start_date_local'))
                activity['start_date'] = (start_dt_utc + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
                activity['start_date_local'] = (start_dt_local_dt + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
                log("  Applied 30-second shift.")

            output_file = f"outputs/{act_id}_with_hr.tcx"
            create_tcx(activity, streams, hr_data, output_file, include_creator=(not args.force_elevation))
            
            a_data = [a for a in streams.get("altitude", {}).get("data", []) if a is not None]
            total_gain_ft = 0
            if a_data:
                gain_m = 0
                for i in range(1, len(a_data)):
                    diff = a_data[i] - a_data[i-1]
                    if diff > 0: gain_m += diff
                total_gain_ft = int(gain_m * 3.28084)
            dist_mi = (streams.get("distance", {}).get("data", [-1])[-1] / 1609.34) if streams.get("distance") else 0

            log("  Attempting upload to Strava...")
            desc = (activity.get("description", "") or "") + "\n\n(Heart rate data added via Fitbit sync)"
            act_type = (activity.get("sport_type") or activity.get("type", "")).replace(" ", "")
            
            upload_resp = strava.upload_activity(
                file_path=output_file, data_type="tcx", name=act_name, 
                description=desc, trainer=activity.get("trainer", False), 
                commute=activity.get("commute", False), gear_id=activity.get("gear_id"), 
                activity_type=act_type
            )
            new_id = str(upload_resp.get("activity_id"))
            log(f"  Upload successful! New Activity: https://www.strava.com/activities/{new_id}")
            if act_type: strava.update_activity(new_id, sport_type=act_type)

            synced = SyncedActivity(
                old_id=act_id, new_id=new_id, name=act_name, date=start_date_local,
                status="pending_cleanup", distance_mi=round(dist_mi, 2),
                duration_min=round(act_duration_sec / 60.0, 1), elevation_gain_ft=total_gain_ft
            )
            db.merge(synced)
            db.commit()
            # AUTO-DECREMENT AND REMOVE FROM FIXABLE
            decrement_scan_count(db, was_fixable=True, act_id=act_id)
        
        except Exception as e:
            if "429" in str(e):
                log(f"  [Error] Fitbit Rate Limit Reached (429). Stopping sync.")
                sys.exit(1)
            log(f"  Error processing activity {act_id}: {e}")
            continue
    
    db.close()

if __name__ == "__main__":
    main()
