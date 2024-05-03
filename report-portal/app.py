from flask import Flask, render_template, jsonify
from flask_mysqldb import MySQL
import MySQLdb.cursors
import logging
import sys  # Added import statement for sys
from datetime import datetime, timedelta
from sshtunnel import SSHTunnelForwarder
import pymysql



# Flask app setup
app = Flask(__name__)

# Configure MySQL connection
app.config['MYSQL_USER'] = 'admin'
app.config['MYSQL_PASSWORD'] = 'Lucidity~1'
app.config['MYSQL_DB'] = 'automation'
app.config['MYSQL_HOST'] = 'e2e-automation-db.cnpggqfyior0.ap-south-1.rds.amazonaws.com'
app.config['MYSQL_PORT'] = 3306

# Initialize MySQL and setup logging
mysql = MySQL(app)
logging.basicConfig(level=logging.INFO)

# SSH tunnel parameters
ssh_host = '13.126.255.1'
ssh_port = 22
ssh_username = 'ubuntu'
ssh_private_key = 'staging.pem'  # Replace with the path to your private key file

# Test database connection before starting the server
def test_db_connection():
    with app.app_context():
        try:
            # Establish SSH tunnel
            with SSHTunnelForwarder(
                (ssh_host, ssh_port),
                ssh_username=ssh_username,
                ssh_pkey=ssh_private_key,
                remote_bind_address=(app.config['MYSQL_HOST'], app.config['MYSQL_PORT'])
            ) as tunnel:
                # Connect to MySQL database through SSH tunnel
                conn = pymysql.connect(
                    host='e2e-automation-db.cnpggqfyior0.ap-south-1.rds.amazonaws.com',
                    port=tunnel.local_bind_port,
                    user=app.config['MYSQL_USER'],
                    passwd=app.config['MYSQL_PASSWORD'],
                    db=app.config['MYSQL_DB']
                )
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.close()
                conn.close()
            app.logger.info("Database connection successful.")
        except Exception as e:
            app.logger.error("Database connection failed: %s", e)
            raise Exception("Database connection failed.")
        
# Function to retrieve instance ID for each failed test
def get_instance_ids(failed_tests):
    with app.app_context():
        try:
            cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
            for test in failed_tests:
                test_run_id = test['id']
                cursor.execute("SELECT instance_id FROM test_runner_instance WHERE test_run_id = %s", (test_run_id,))
                instance_result = cursor.fetchone()
                if instance_result:
                    test['instance_id'] = instance_result['instance_id']
            cursor.close()
        except Exception as e:
            app.logger.error("Error fetching instance IDs: %s", e)        

# Home page route
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/get_test_stats')
def get_test_stats():
    try:
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)  # Use DictCursor
        
        # Fetch the last 100 test operation IDs
        cursor.execute("""
            SELECT id
            FROM test_operations_run
            ORDER BY id DESC
            LIMIT 100
        """)
        last_100_ids = [row['id'] for row in cursor.fetchall()]  # Collect the IDs
        
        # Fetch the counts of statuses for those IDs
        if last_100_ids:
            query = """
                SELECT status, COUNT(*) AS count
                FROM test_operations_run
                WHERE id IN (%s)
                GROUP BY status
            """ % ','.join(map(str, last_100_ids))
            cursor.execute(query)
            status_counts = cursor.fetchall()  # Fetch the results
        
            # Initialize a dictionary to store the counts for each status
            stats = {
                'COMPLETED': 0,
                'FAILED': 0,
                'IN_PROGRESS': 0,
                'CREATED': 0
            }
        
            # Fill the dictionary with the counts from the query results
            for row in status_counts:
                status = row['status']
                count = row['count']
                if status in stats:
                    stats[status] += count
        
            return jsonify(stats)  # Return the counts as a JSON object
        else:
            return jsonify({"error": "No data available"}), 500
    except Exception as e:
        app.logger.error("Error fetching test stats: %s", e)
        return jsonify({"error": str(e)}), 500

# Autoscaler page route
@app.route('/autoscaler')
def autoscaler():
    try:
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)  # Use DictCursor
        # Fetch the last 10 runs for each service
        agent_query = """
        SELECT id, name, test_result 
        FROM test_group_run 
        WHERE test_plan_name = 'test_plan_001' 
        ORDER BY id DESC 
        LIMIT 10
        """
        cursor.execute(agent_query)
        agent_runs = cursor.fetchall()

        agent_light_query = """
        SELECT id, name, test_result 
        FROM test_group_run 
        WHERE test_plan_name = 'agent_light_plan' 
        ORDER BY id DESC 
        LIMIT 10
        """
        cursor.execute(agent_light_query)
        agent_light_runs = cursor.fetchall()

        orchestrator_query = """
        SELECT id, name, test_result 
        FROM test_group_run 
        WHERE test_plan_name = 'ORCHESTRATOR_CRON_TEST' 
        ORDER BY id DESC 
        LIMIT 10
        """
        cursor.execute(orchestrator_query)
        orchestrator_runs = cursor.fetchall()

        return render_template('autoscaler.html', 
                               agent_runs=agent_runs, 
                               agent_light_runs=agent_light_runs, 
                               orchestrator_runs=orchestrator_runs)

    except Exception as e:
        app.logger.error("Error fetching data for autoscaler: %s", e)
        return "An error occurred", 500

# Test Results page route
@app.route('/test_results/<int:test_group_id>')
def test_results(test_group_id):
    try:
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)  # Use DictCursor
        
        # Query to fetch failed test cases and messages
        query = """
        SELECT id, operation_name, message, meta, cloud_provider_image_id, cloud_platform, os_type
        FROM test_operations_run
        WHERE test_group_id = %s AND status = 'FAILED'
        """
        cursor.execute(query, (test_group_id,))
        failed_tests = cursor.fetchall()

        # Call function to retrieve instance IDs for failed tests
        get_instance_ids(failed_tests)

        # Count test results
        query = """
        SELECT status 
        FROM test_operations_run 
        WHERE test_group_id = %s
        """
        cursor.execute(query, (test_group_id,))
        operations = cursor.fetchall()
        passed = sum(1 for op in operations if op['status'] == 'COMPLETED')
        failed = sum(1 for op in operations if op['status'] == 'FAILED')
        pending = sum(1 for op in operations if op['status'] == 'PENDING')

        return render_template('test_results.html', 
                                test_group_id=test_group_id, 
                                passed=passed, 
                                failed=failed, 
                                pending=pending,
                                failed_tests=failed_tests)

    except Exception as e:
        app.logger.error("Error fetching data for test results: %s", e)
        return "An error occurred", 500
    
# Dashboard route
@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

# Error handling for 500 Internal Server Errors
@app.errorhandler(500)
def internal_server_error(e):
    app.logger.error("500 error: %s", e)
    return "An internal server error occurred", 500

@app.route('/get_test_cases_stats')
def get_test_cases_stats():
    try:
        with app.app_context():
            cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)  # Use DictCursor
            # Query to fetch test cases stats for the last 7 days
            query = """
            SELECT
                SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) AS PASSED,
                SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS FAILED,
                SUM(CASE WHEN status = 'IN_PROGRESS' THEN 1 ELSE 0 END) AS IN_PROGRESS
            FROM test_operations_run
            WHERE started_at >= %s
            """
            start_date = datetime.now() - timedelta(days=7)
            cursor.execute(query, (start_date,))
            test_cases_data = cursor.fetchone()
            return jsonify(test_cases_data)
    except Exception as e:
        app.logger.error("Error fetching test cases stats: %s", e)
        return jsonify({"error": str(e)}), 500

# Route to fetch test runs stats
@app.route('/get_test_runs_stats')
def get_test_runs_stats():
    try:
        with app.app_context():
            cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)  # Use DictCursor
            # Query to fetch test runs stats for the last 7 days
            query = """
            SELECT
                SUM(CASE WHEN test_result = 'PASS' THEN 1 ELSE 0 END) AS PASSED,
                SUM(CASE WHEN test_result = 'FAIL' THEN 1 ELSE 0 END) AS FAILED,
                SUM(CASE WHEN test_result = 'PENDING' THEN 1 ELSE 0 END) AS PENDING
            FROM test_group_run
            WHERE created_at >= %s
            """
            start_date = datetime.now() - timedelta(days=7)
            cursor.execute(query, (start_date,))
            test_runs_data = cursor.fetchone()
            return jsonify(test_runs_data)
    except Exception as e:
        app.logger.error("Error fetching test runs stats: %s", e)
        return jsonify({"error": str(e)}), 500
    
# Route to fetch service counts
@app.route('/get_service_counts')
def get_service_counts():
    try:
        with app.app_context():
            cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
            # Query to fetch service counts
            query = """
            SELECT
                service,
                COUNT(*) AS count
            FROM test_plans
            GROUP BY service
            """
            cursor.execute(query)
            service_counts = cursor.fetchall()
            return jsonify(service_counts)
    except Exception as e:
        app.logger.error("Error fetching service counts: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route('/test_history/<cloud_provider_image_id>')
def test_history(cloud_provider_image_id):
    try:
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

        # Query to fetch the last 20 runs for a given cloud_provider_image_id
        query = """
        SELECT 
            id, 
            operation_name, 
            status, 
            started_at
        FROM 
            test_operations_run 
        WHERE 
            cloud_provider_image_id = %s 
        ORDER BY 
            started_at DESC 
        LIMIT 20
        """
        cursor.execute(query, (cloud_provider_image_id,))
        test_history_data = cursor.fetchall()

        return render_template(
            'test_history.html', 
            cloud_provider_image_id=cloud_provider_image_id, 
            test_history=test_history_data
        )

    except Exception as e:
        app.logger.error("Error fetching test history for cloud_provider_image_id %s: %s", cloud_provider_image_id, e)
        return "An error occurred", 500



# Error handling for 500 Internal Server Errors
@app.errorhandler(500)
def internal_server_error(e):
    app.logger.error("500 error: %s", e)
    return "An internal server error occurred", 500

# Start the Flask server
if __name__ == "__main__":
    try:
        test_db_connection()  # Test the DB connection before starting
        app.run(debug=True)
    except Exception as e:
        app.logger.error("Failed to start the app due to a DB connection issue: %s", e)
        sys.exit(1)  # Exit with a non-zero code to indicate failure
