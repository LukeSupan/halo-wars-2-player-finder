# halo-wars-2-player-finder
finds players using the haloapi for stat tracking purposes

## Setup

1. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env`, then replace the placeholder value:

   ```env
   HALO_API_KEY=your_halo_api_key_here
   ```

3. Edit `tracked_players.txt` to change who gets checked. Add one Xbox
   gamertag per line.

4. Run the finder:

   ```powershell
   python main.py
   ```

The generated `group_matches_export.json` file is ignored by Git so local test
runs do not get committed by accident.
