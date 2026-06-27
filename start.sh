#!/bin/bash
# Quick startup script for CineMatch

echo "🚀 Starting CineMatch..."
echo ""
echo "Starting backend on port 8000..."
cd backend
python -m uvicorn main:app --reload &
BACKEND_PID=$!

echo "Waiting for backend to start..."
sleep 3

echo ""
echo "Starting frontend on port 5173..."
cd ../frontend
npm run dev &
FRONTEND_PID=$!

echo ""
echo "✅ CineMatch is running!"
echo ""
echo "🌐 Open: http://localhost:5173"
echo ""
echo "Ctrl+C to stop"
wait
