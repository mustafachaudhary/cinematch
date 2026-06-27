import React, { useState, useEffect } from 'react';

export default function ProfileDashboard() {
  // Authentication State
  const [user, setUser] = useState(null);
  const [authForm, setAuthForm] = useState({ username: '', password: '' });
  
  // App Logs & Analytics State
  const [logs, setLogs] = useState([]);
  const [ratingDistribution, setRatingDistribution] = useState(Array(10).fill(0));
  const [loading, setLoading] = useState(true);

  // 4 Favorites State
  const [favorites, setFavorites] = useState([null, null, null, null]);
  const [activeFavIndex, setActiveFavIndex] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);

  // Letterboxd Import File State
  const [file, setFile] = useState(null);
  const [importStatus, setImportStatus] = useState('');

  // Hydrate Data on Mount / Auth changes
  useEffect(() => {
    if (user) {
      fetchUserLogsAndAnalytics();
    }
  }, [user]);

  const fetchUserLogsAndAnalytics = async () => {
    setLoading(true);
    try {
      const res = await fetch("http://127.0.0.1:8000/logs/");
      const data = await res.res ? await res.json() : await res.json();
      setLogs(data);

      // Process 10-point distribution matrix for the SVG bar chart
      const distribution = Array(10).fill(0);
      data.forEach(item => {
        const r = Math.min(Math.max(Math.round(item.rating), 1), 10);
        distribution[r - 1] += 1;
      });
      setRatingDistribution(distribution);
    } catch (err) {
      console.error("Could not fetch analytical logs:", err);
    } finally {
      setLoading(false);
    }
  };

  // Simulated Account Login Execution
  const handleLogin = (e) => {
    e.preventDefault();
    if (authForm.username.trim()) {
      setUser({ name: authForm.username });
    }
  };

  // TMDB Fast-Search for Selection Panel
  const searchFavoriteMovie = async (val) => {
    setSearchQuery(val);
    if (val.trim().length < 2) return;
    try {
      const res = await fetch(`http://127.0.0.1:8000/movies/tmdb-search?q=${encodeURIComponent(val)}`);
      const data = await res.json();
      setSearchResults(data || []);
    } catch (err) {
      console.error("Search query execution failed:", err);
    }
  };

  // Pin Movie to specific slot
  const selectFavorite = (movie) => {
    const updated = [...favorites];
    updated[activeFavIndex] = movie;
    setFavorites(updated);
    setActiveFavIndex(null);
    setSearchQuery('');
    setSearchResults([]);
  };

  // Execute Letterboxd CSV Parsing Stream
  const handleLetterboxdSync = async (e) => {
    e.preventDefault();
    if (!file) return;
    setImportStatus('processing');
    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await fetch("http://127.0.0.1:8000/logs/import-letterboxd", {
        method: "POST",
        body: formData,
      });
      const data = await response.json();
      setImportStatus(`Successfully synced ${data.records_imported} movies. Skipped ${data.records_skipped_already_logged} duplicates.`);
      fetchUserLogsAndAnalytics(); // Update numbers instantly
    } catch (err) {
      setImportStatus('Import failed. Ensure formatting parameters match rules.');
    }
  };

  if (!user) {
    return (
      <div className="max-w-md mx-auto my-12 bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-xl">
        <h2 className="text-2xl font-black text-white text-center mb-2">Access CineMatch.ai</h2>
        <p className="text-xs text-slate-400 text-center mb-6">Log into your local intelligence database station.</p>
        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label className="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Station ID / Username</label>
            <input 
              type="text" required value={authForm.username}
              onChange={e => setAuthForm({...authForm, username: e.target.value})}
              className="w-full bg-slate-950 border border-slate-800 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
              placeholder="e.g., Administrator"
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Security Token / Password</label>
            <input 
              type="password" required value={authForm.password}
              onChange={e => setAuthForm({...authForm, password: e.target.value})}
              className="w-full bg-slate-950 border border-slate-800 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
              placeholder="••••••••"
            />
          </div>
          <button type="submit" className="w-full bg-blue-600 hover:bg-blue-500 font-bold text-sm text-white py-2.5 rounded-lg transition-colors">
            Authorize Connection
          </button>
        </form>
      </div>
    );
  }

  const maxFrequency = Math.max(...ratingDistribution, 1);

  return (
    <div className="max-w-5xl mx-auto space-y-10 p-4 text-white">
      {/* Upper Dashboard Meta Info */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center bg-slate-900 border border-slate-800 p-6 rounded-2xl gap-4">
        <div>
          <h1 className="text-3xl font-black">Welcome back, {user.name}</h1>
          <p className="text-sm text-slate-400">System engine optimized. Vectors actively loaded via SQLite database file.</p>
        </div>
        <div className="bg-slate-950 px-5 py-3 rounded-xl border border-slate-800 text-center">
          <p className="text-[10px] uppercase font-bold text-slate-500 tracking-widest">Aggregate Records</p>
          <p className="text-3xl font-mono font-black text-blue-400">{loading ? '...' : logs.length}</p>
        </div>
      </div>

      {/* 4 Favorite Movies Slots */}
      <div>
        <h2 className="text-xs uppercase font-extrabold text-slate-400 tracking-widest mb-4">Four Favorite Movies Layout</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {favorites.map((fav, index) => (
            <div 
              key={index}
              onClick={() => setActiveFavIndex(index)}
              className="aspect-[2/3] bg-slate-900 border-2 border-dashed border-slate-800 rounded-xl relative overflow-hidden group cursor-pointer hover:border-blue-500/50 transition-all flex flex-col items-center justify-center p-2"
            >
              {fav ? (
                <>
                  <img src={`https://image.tmdb.org/t/p/w500${fav.poster_path}`} alt={fav.title} className="w-full h-full object-cover absolute inset-0 group-hover:scale-105 transition-transform" />
                  <div className="absolute inset-0 bg-black/70 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                    <span className="text-xs font-bold text-red-400">Replace Slot</span>
                  </div>
                </>
              ) : (
                <div className="text-center space-y-1 text-slate-500 group-hover:text-slate-300">
                  <span className="text-2xl font-light">+</span>
                  <p className="text-[10px] font-bold uppercase tracking-wider">Assign Favorite</p>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Star Distribution Histogram & Analytics Graph */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 md:col-span-2 flex flex-col">
          <h3 className="text-sm font-bold uppercase text-slate-400 tracking-wider mb-6">Star Rating Distribution Frequency</h3>
          
          {/* SVG / Pure Layout Horizontal Bar Graph charts */}
          <div className="flex-1 flex items-end justify-between h-48 pt-4 px-2 border-b border-slate-800">
            {ratingDistribution.map((count, idx) => {
              const pct = (count / maxFrequency) * 100;
              return (
                <div key={idx} className="flex flex-col items-center flex-1 group mx-1">
                  <div className="w-full bg-slate-950 rounded-t-md relative flex items-end" style={{ height: '140px' }}>
                    <div 
                      className="w-full bg-gradient-to-t from-blue-600 to-indigo-400 rounded-t-md transition-all duration-500 group-hover:from-blue-500 group-hover:to-cyan-400"
                      style={{ height: `${pct}%` }}
                    />
                    {/* Tooltip counter hover overlay text */}
                    <span className="absolute -top-6 left-1/2 -translate-x-1/2 text-[10px] font-mono bg-slate-800 text-white px-1.5 py-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">
                      {count} logs
                    </span>
                  </div>
                  <span className="text-xs font-mono font-bold mt-2 text-slate-500 group-hover:text-white">★{idx + 1}</span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Letterboxd Account Data Portability Form Module */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 flex flex-col justify-between">
          <div>
            <h3 className="text-sm font-bold uppercase text-slate-400 tracking-wider mb-2">History Sync Hub</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Inject your extracted Letterboxd accounts data directly into the application pipeline framework instantly.
            </p>
          </div>

          <form onSubmit={handleLetterboxdSync} className="mt-4 space-y-3">
            <div className="bg-slate-950 border border-slate-800 rounded-xl p-3 text-center cursor-pointer hover:border-slate-700 transition-colors relative">
              <input 
                type="file" accept=".csv" required
                onChange={e => setFile(e.target.files[0])}
                className="absolute inset-0 opacity-0 cursor-pointer w-full h-full"
              />
              <p className="text-xs font-mono truncate text-slate-400">
                {file ? file.name : "Select ratings.csv file"}
              </p>
            </div>
            
            <button 
              type="submit" disabled={!file || importStatus === 'processing'}
              className="w-full bg-indigo-600 disabled:bg-slate-800 disabled:text-slate-600 hover:bg-indigo-500 text-white text-xs font-bold py-2 rounded-xl transition-all"
            >
              {importStatus === 'processing' ? "Ingesting Documents Matrix..." : "Start Neural History Sync"}
            </button>
          </form>

          {importStatus && importStatus !== 'processing' && (
            <div className="text-[11px] bg-slate-950 border border-slate-800 text-slate-300 p-2.5 rounded-lg mt-3 font-mono leading-tight">
              {importStatus}
            </div>
          )}
        </div>
      </div>

      {/* Pop-up Favorite Slot Selection Modal Overlay */}
      {activeFavIndex !== null && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md p-6 relative shadow-2xl">
            <button onClick={() => setActiveFavIndex(null)} className="absolute top-4 right-4 text-slate-500 hover:text-white font-bold">✕</button>
            <h3 className="text-lg font-black mb-1">Set Slot #{activeFavIndex + 1} Favorite</h3>
            <p className="text-xs text-slate-400 mb-4">Query local TMDB indices to map file pointers.</p>
            
            <input 
              type="text" value={searchQuery} onChange={e => searchFavoriteMovie(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 text-white rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500 mb-4"
              placeholder="Search movie title..."
            />

            <div className="max-h-60 overflow-y-auto space-y-2 pr-1 custom-scrollbar">
              {searchResults.map((movie) => (
                <div 
                  key={movie.tmdb_id} onClick={() => selectFavorite(movie)}
                  className="flex items-center space-x-3 p-2 rounded-lg bg-slate-950 border border-slate-800 hover:border-blue-500/50 cursor-pointer transition-all"
                >
                  {movie.poster_path && (
                    <img src={`https://image.tmdb.org/t/p/w92${movie.poster_path}`} alt="" className="w-10 h-14 object-cover rounded" />
                  )}
                  <div>
                    <h4 className="text-xs font-bold text-white truncate max-w-[260px]">{movie.title}</h4>
                    <p className="text-[10px] text-slate-500">{movie.release_year || 'Unknown Year'}</p>
                  </div>
                </div>
              ))}
              {searchQuery.length >= 2 && searchResults.length === 0 && (
                <p className="text-xs text-center text-slate-600 py-4">No matching records registered.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
