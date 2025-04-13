import json
import boto3
from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_cors import CORS
import feedparser
from datetime import datetime, timedelta
import uuid
from apscheduler.schedulers.background import BackgroundScheduler
import logging
import pickle
import os
from boto3.dynamodb.conditions import Attr

# Initialize Flask App
app = Flask(__name__, static_folder='images')
CORS(app, resources={r"/*": {"origins": "*"}}, headers="Content-Type", methods=["POST", "OPTIONS"])

# AWS Services
dynamodb = boto3.resource('dynamodb', region_name='eu-west-1')
s3 = boto3.client('s3')

# DynamoDB table and S3 bucket
table_name = "ClassifiedNews"
table = dynamodb.Table(table_name)
s3_bucket = "googlerss-classified-output"

# Load model and vectorizer from EC2 instance
with open("model/classifier.pkl", "rb") as model_file:
    classifier = pickle.load(model_file)
with open("model/tfidf_vectorizer.pkl", "rb") as vectorizer_file:
    tfidf_vectorizer = pickle.load(vectorizer_file)

# Logging setup
logging.basicConfig(
    filename="app.log",  
    level=logging.INFO,  
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Function to fetch and classify news every hour
def fetch_and_classify_news():
    try:
        rss_url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(rss_url)
        current_time = datetime.utcnow()

        for entry in feed.entries:
            title = entry.title
            news_id = str(uuid.uuid4())
            timestamp = current_time.isoformat()

            # Predict category
            vectorized_text = tfidf_vectorizer.transform([title])
            prediction = classifier.predict(vectorized_text)[0]

            # Store in DynamoDB
            table.put_item(Item={
                "news_id": news_id,
                "title": title,
                "prediction": prediction,
                "timestamp": timestamp
            })

            logger.info(f"Writing to DynamoDB: {news_id}, {title}, {prediction}, {timestamp}")

        logger.info("News fetched and classified successfully!")
    except Exception as e:
        logger.error(f"Error in fetch_and_classify_news: {e}")

# Scheduler to automate classification every hour
scheduler = BackgroundScheduler()
scheduler.add_job(func=fetch_and_classify_news, trigger="interval", hours=1)
scheduler.start()

# Route for the main page
@app.route('/')
def home():
    return render_template('main.html')

# Route to get latest news
@app.route('/predict', methods=['POST', 'GET'])
def get_latest_news():
    try:
        current_time = datetime.utcnow()
        one_hour_ago = current_time - timedelta(hours=1)

        response = table.scan(
            FilterExpression=Attr('timestamp').between(one_hour_ago.isoformat(), current_time.isoformat())
        )
        items = response.get("Items", [])

        logger.info(f"Filtering from {one_hour_ago.isoformat()} to {current_time.isoformat()}")
        logger.info(f"DynamoDB Response: {response}")

        if request.method == "POST":
            if items:
                file_name = f"classified_news_{current_time.strftime('%Y%m%d%H%M%S')}.json"
                s3.put_object(
                    Bucket=s3_bucket,
                    Key=file_name,
                    Body=json.dumps(items),
                    ContentType="application/json"
                )
                logger.info(f"Stored classified news in S3: {file_name}")

            # Return classified news in JSON format
            return jsonify({"classified_news": items or []})

        # Render main.html due to the initial GET request
        return render_template("main.html", classified_news=items)

    except Exception as e:
        logger.error(f"Error in get_latest_news: {e}")
        return jsonify({"error": str(e)}), 500

# Gracefully handle shutdown
@app.route('/shutdown', methods=['POST'])
def shutdown():
    scheduler.shutdown()
    return "Scheduler stopped successfully", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
